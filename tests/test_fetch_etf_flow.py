import html
import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from scripts import fetch_etf_flow


class FetchEtfFlowTests(unittest.TestCase):
    AS_OF = date(2026, 7, 31)
    NOW = datetime(2026, 7, 31, 20, 0, tzinfo=timezone.utc)
    FETCHED_AT = "2026-07-31T20:00:00Z"

    FUND_HEADERS = [
        "IBIT",
        "FBTC",
        "BITB",
        "ARKB",
        "BTCO",
        "EZBC",
        "BRRR",
        "HODL",
        "BTCW",
        "MSBT",
        "GBTC",
        "BTC",
    ]
    HEADERS = ["Date", *FUND_HEADERS, "Total"]

    @classmethod
    def make_row(cls, display_date, total, **overrides):
        row = {header: "0.0" for header in cls.FUND_HEADERS}
        row.update({"Date": display_date, "IBIT": total, "Total": total})
        row.update(overrides)
        return row

    @classmethod
    def final_history_rows(cls):
        """Newest-first rows, matching the order used by the source page."""

        return [
            cls.make_row("31 Jul 2026", "(265.4)"),
            cls.make_row("30 Jul 2026", "233.1"),
            cls.make_row("29 Jul 2026", "32.1"),
            cls.make_row("28 Jul 2026", "(49.7)"),
            cls.make_row("27 Jul 2026", "(11.6)", BTCO="-"),
            cls.make_row("24 Jul 2026", "20.0"),
            cls.make_row("23 Jul 2026", "10.0"),
        ]

    @classmethod
    def intraday_rows(cls):
        rows = cls.final_history_rows()
        older_rows = [row for row in rows if row["Date"] != "31 Jul 2026"]
        older_rows = [row for row in older_rows if row["Date"] != "30 Jul 2026"]
        provisional = cls.make_row(
            "30 Jul 2026",
            "49.7",
            IBIT="-",
            FBTC="49.7",
        )
        return [provisional, *older_rows]

    @classmethod
    def make_html(
        cls,
        rows,
        headers=None,
        *,
        include_decoy=True,
        nested_header_text=False,
    ):
        headers = headers or cls.HEADERS
        parts = ["<!doctype html><html><body>"]
        if include_decoy:
            parts.append(
                "<table id='decoy'><tr><th>Date</th><th>Price</th></tr>"
                "<tr><td>31 Jul 2026</td><td>123</td></tr></table>"
            )
        parts.append("<table id='etf-flow'><thead><tr>")
        for header in headers:
            label = html.escape(header)
            if nested_header_text:
                parts.append(f"<th><span>{label}</span></th>")
            else:
                parts.append(f"<th>{label}</th>")
        parts.append("</tr></thead><tbody>")
        for row in rows:
            parts.append("<tr>")
            for header in headers:
                value = html.escape(str(row[header]))
                parts.append(f"<td><span>{value}</span></td>")
            parts.append("</tr>")
        parts.append("</tbody></table></body></html>")
        return "".join(parts)

    @staticmethod
    def item_value(item, key):
        if isinstance(item, dict):
            return item[key]
        return getattr(item, key)

    @classmethod
    def normalized_item_date(cls, item):
        value = cls.item_value(item, "date")
        return value.isoformat() if isinstance(value, date) else value

    @classmethod
    def find_parsed_row(cls, parsed_page, expected_date):
        for row in parsed_page.rows:
            if cls.normalized_item_date(row) == expected_date:
                return row
        raise AssertionError(f"fixture row not found: {expected_date}")

    def parse_final_page(self):
        return fetch_etf_flow.parse_farside_html(
            self.make_html(self.final_history_rows())
        )

    def build(self, parsed_page=None, requested_as_of=None):
        return fetch_etf_flow.build_result(
            parsed_page or self.parse_final_page(),
            requested_as_of or self.AS_OF,
            self.NOW,
            cache_status={"used_cache": True, "status": "使用缓存"},
            fetched_at_utc=self.FETCHED_AT,
            network_request_count=0,
        )

    # 1. Positive values are numeric inflows.
    def test_parse_positive_value(self):
        self.assertEqual(fetch_etf_flow.parse_flow_value("233.1"), 233.1)
        self.assertEqual(fetch_etf_flow.parse_flow_value(" +233.1 "), 233.1)

    # 2. Parentheses denote an outflow, not an invalid value.
    def test_parse_parenthesized_negative_value(self):
        self.assertEqual(fetch_etf_flow.parse_flow_value("(265.4)"), -265.4)

    # 3. An explicit zero is valid and must not be treated as missing.
    def test_parse_explicit_zero(self):
        self.assertEqual(fetch_etf_flow.parse_flow_value("0"), 0.0)
        self.assertEqual(fetch_etf_flow.parse_flow_value("0.0"), 0.0)
        self.assertIsNotNone(fetch_etf_flow.parse_flow_value("0.0"))

    # 4. A dash is missing data, never a synthetic zero.
    def test_parse_dash_as_missing(self):
        self.assertIsNone(fetch_etf_flow.parse_flow_value("-"))
        self.assertIsNone(fetch_etf_flow.parse_flow_value(" - "))

    # 5. A numeric Total with a missing core fund is provisional.
    def test_latest_core_fund_missing_is_provisional(self):
        parsed = fetch_etf_flow.parse_farside_html(
            self.make_html(self.intraday_rows())
        )
        row = self.find_parsed_row(parsed, "2026-07-30")

        self.assertEqual(self.item_value(row, "total"), 49.7)
        self.assertEqual(self.item_value(row, "status"), "provisional")
        self.assertEqual(self.item_value(row, "missing_core_funds"), ["IBIT"])
        self.assertEqual(self.item_value(row, "missing_funds"), ["IBIT"])

        result = self.build(parsed, date(2026, 7, 30))
        self.assertTrue(result["provisional_latest"])
        self.assertEqual(result["latest_page_row"]["status"], "provisional")
        self.assertEqual(fetch_etf_flow.determine_exit_code(result), 2)
        markdown = fetch_etf_flow.render_markdown(result)
        self.assertIn("全部缺失基金：IBIT", markdown)
        self.assertIn("该暂报数据没有作为正式日流量使用", markdown)

    # 6. Missing a secondary fund does not downgrade an otherwise complete row.
    def test_secondary_fund_missing_with_complete_core_is_complete(self):
        parsed = self.parse_final_page()
        row = self.find_parsed_row(parsed, "2026-07-27")

        self.assertEqual(self.item_value(row, "total"), -11.6)
        self.assertEqual(self.item_value(row, "status"), "complete")
        self.assertEqual(self.item_value(row, "missing_core_funds"), [])
        self.assertEqual(self.item_value(row, "missing_funds"), ["BTCO"])

        result = self.build(parsed)
        self.assertEqual(result["warnings"], [])
        self.assertEqual(fetch_etf_flow.determine_exit_code(result), 0)

    # 7. A missing published Total is unavailable; fund cells must not be summed.
    def test_missing_total_is_unavailable(self):
        unavailable = self.make_row(
            "31 Jul 2026",
            "-",
            IBIT="12.3",
        )
        older_rows = [
            row
            for row in self.final_history_rows()
            if row["Date"] != "31 Jul 2026"
        ]
        parsed = fetch_etf_flow.parse_farside_html(
            self.make_html([unavailable, *older_rows])
        )
        row = self.find_parsed_row(parsed, "2026-07-31")

        self.assertIsNone(self.item_value(row, "total"))
        self.assertEqual(self.item_value(row, "status"), "unavailable")
        self.assertEqual(self.item_value(row, "missing_core_funds"), [])

        result = self.build(parsed)
        self.assertEqual(result["latest_page_row"]["status"], "unavailable")
        self.assertEqual(result["latest_complete_row"]["date"], "2026-07-30")
        self.assertTrue(result["warnings"])
        self.assertEqual(fetch_etf_flow.determine_exit_code(result), 2)

    # 8. The latest complete observation skips a newer provisional page row.
    def test_selects_latest_complete_row(self):
        parsed = fetch_etf_flow.parse_farside_html(
            self.make_html(self.intraday_rows())
        )
        result = self.build(parsed, date(2026, 7, 30))

        self.assertEqual(result["latest_page_row"]["date"], "2026-07-30")
        self.assertEqual(result["latest_complete_row"]["date"], "2026-07-29")
        self.assertEqual(result["previous_complete_row"]["date"], "2026-07-28")
        self.assertEqual(result["flows"]["latest"], 32.1)
        self.assertEqual(result["flows"]["previous"], -49.7)

    # 9. 3D is the sum of the latest three complete US trading-day rows.
    def test_three_day_cumulative_flow(self):
        result = self.build()

        self.assertAlmostEqual(result["flows"]["cumulative_3d"], -0.2)
        self.assertEqual(
            result["quality_checks"]["complete_dates_3d"],
            ["2026-07-29", "2026-07-30", "2026-07-31"],
        )

    # 10. 5D similarly uses complete trading-day rows, not calendar-day slots.
    def test_five_day_cumulative_flow(self):
        result = self.build()

        self.assertAlmostEqual(result["flows"]["cumulative_5d"], -61.5)
        self.assertEqual(
            result["quality_checks"]["complete_dates_5d"],
            [
                "2026-07-27",
                "2026-07-28",
                "2026-07-29",
                "2026-07-30",
                "2026-07-31",
            ],
        )

    # 11. The verified 30/31 July pair flips from inflow to outflow.
    def test_sign_flip_and_positive_negative_counts(self):
        result = self.build()

        self.assertEqual(result["latest_complete_row"]["date"], "2026-07-31")
        self.assertEqual(result["previous_complete_row"]["date"], "2026-07-30")
        self.assertEqual(result["flows"]["latest"], -265.4)
        self.assertEqual(result["flows"]["previous"], 233.1)
        self.assertTrue(result["continuity"]["sign_flip"])
        self.assertEqual(result["continuity"]["positive_days_3d"], 2)
        self.assertEqual(result["continuity"]["negative_days_3d"], 1)
        self.assertEqual(result["continuity"]["positive_days_5d"], 2)
        self.assertEqual(result["continuity"]["negative_days_5d"], 3)

    # 12. A provisional page row is disclosed but excluded from both windows.
    def test_provisional_row_is_excluded_from_cumulative_flows(self):
        parsed = fetch_etf_flow.parse_farside_html(
            self.make_html(self.intraday_rows())
        )
        result = self.build(parsed, date(2026, 7, 30))

        self.assertEqual(result["latest_page_row"]["total"], 49.7)
        self.assertTrue(result["provisional_latest"])
        self.assertAlmostEqual(result["flows"]["cumulative_3d"], -29.2)
        self.assertAlmostEqual(result["flows"]["cumulative_5d"], 0.8)
        self.assertNotIn(
            "2026-07-30", result["quality_checks"]["complete_dates_3d"]
        )
        self.assertNotIn(
            "2026-07-30", result["quality_checks"]["complete_dates_5d"]
        )

    # 13. Historical --as-of analysis must not see a later complete row.
    def test_historical_as_of_excludes_future_rows(self):
        result = self.build(requested_as_of=date(2026, 7, 30))

        self.assertEqual(result["requested_as_of"], "2026-07-30")
        self.assertEqual(result["latest_page_row"]["date"], "2026-07-30")
        self.assertEqual(result["latest_complete_row"]["date"], "2026-07-30")
        self.assertEqual(result["previous_complete_row"]["date"], "2026-07-29")
        self.assertEqual(result["flows"]["latest"], 233.1)
        self.assertEqual(result["flows"]["previous"], 32.1)
        self.assertAlmostEqual(result["flows"]["cumulative_3d"], 215.5)
        self.assertAlmostEqual(result["flows"]["cumulative_5d"], 223.9)
        self.assertFalse(result["continuity"]["sign_flip"])
        self.assertNotIn(
            "2026-07-31", result["quality_checks"]["complete_dates_5d"]
        )

    # 14. Header names, not hard-coded positions, determine column mapping.
    def test_html_column_reordering_and_decoy_table(self):
        reordered = [
            "Total",
            "GBTC",
            "Date",
            "BTC",
            "IBIT",
            "MSBT",
            "FBTC",
            "BTCO",
            "ARKB",
            "BITB",
            "EZBC",
            "BRRR",
            "HODL",
            "BTCW",
        ]
        parsed = fetch_etf_flow.parse_farside_html(
            self.make_html(
                self.final_history_rows(),
                headers=reordered,
                include_decoy=True,
                nested_header_text=True,
            )
        )
        result = self.build(parsed)
        row = self.find_parsed_row(parsed, "2026-07-30")
        funds = self.item_value(row, "funds")

        self.assertEqual(funds["IBIT"], 233.1)
        self.assertEqual(self.item_value(row, "total"), 233.1)
        self.assertEqual(self.item_value(row, "status"), "complete")
        self.assertEqual(result["latest_complete_row"]["date"], "2026-07-31")
        self.assertEqual(result["flows"]["latest"], -265.4)

    def test_real_farside_multiline_header_layout(self):
        parts = ["<html><body><table>"]
        parts.append(
            "<tr>" + "<th></th>" * (len(self.HEADERS) - 1) + "<th>Total</th></tr>"
        )
        parts.append(
            "<tr><th></th>"
            + "".join(f"<th>{fund}</th>" for fund in self.FUND_HEADERS)
            + "<th></th></tr>"
        )
        parts.append(
            "<tr><th>Fee</th>"
            + "<td>0.25%</td>" * len(self.FUND_HEADERS)
            + "<td></td></tr>"
        )
        for row in self.final_history_rows():
            parts.append("<tr>")
            parts.append(f"<td>{html.escape(row['Date'])}</td>")
            parts.extend(
                f"<td>{html.escape(str(row[fund]))}</td>"
                for fund in self.FUND_HEADERS
            )
            parts.append(f"<td>{html.escape(str(row['Total']))}</td></tr>")
        parts.append("</table></body></html>")

        parsed = fetch_etf_flow.parse_farside_html("".join(parts))
        result = self.build(parsed)

        self.assertEqual(result["latest_page_date"], "2026-07-31")
        self.assertEqual(result["flows"]["latest"], -265.4)
        self.assertEqual(result["flows"]["previous"], 233.1)
        self.assertTrue(result["continuity"]["sign_flip"])

    # 15. Semantically malformed HTML fails with a controlled domain error.
    def test_malformed_html_fails_reasonably(self):
        malformed_fixtures = [
            (
                "missing Total header",
                "<html><table><tr><th>Date</th><th>IBIT</th></tr>"
                "<tr><td>31 Jul 2026</td><td>1.0</td></tr></table></html>",
            ),
            (
                "no valid data rows",
                "<html><table><tr><th>Date</th><th>IBIT</th><th>Total</th></tr>"
                "<tr><td>not-a-date</td><td>1.0</td></tr></table></html>",
            ),
        ]

        for label, fixture in malformed_fixtures:
            with self.subTest(label=label):
                with self.assertRaises(fetch_etf_flow.EtfFlowError) as raised:
                    fetch_etf_flow.parse_farside_html(fixture)
                self.assertTrue(str(raised.exception).strip())

    def test_json_and_markdown_outputs_are_generated_offline(self):
        result = self.build()
        markdown = fetch_etf_flow.render_markdown(result)

        self.assertIn("2026-07-31", markdown)
        self.assertIn("-$265.4M", markdown)
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            fetch_etf_flow.write_outputs(result, output_dir)
            json_path = output_dir / "latest.json"
            markdown_path = output_dir / "latest.md"
            loaded = json.loads(json_path.read_text(encoding="utf-8"))

            self.assertEqual(loaded["flows"]["latest"], -265.4)
            self.assertEqual(loaded["flows"]["previous"], 233.1)
            self.assertEqual(markdown_path.read_text(encoding="utf-8"), markdown)

    def test_cache_hit_and_fifteen_minute_expiry(self):
        html_text = self.make_html(self.final_history_rows())
        calls = []

        def fake_fetcher(url, timeout):
            calls.append((url, timeout))
            return html_text

        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            _, first_status, _, first_count = fetch_etf_flow.obtain_html(
                output_dir,
                True,
                self.NOW,
                fetcher=fake_fetcher,
            )
            self.assertEqual(first_count, 1)
            self.assertFalse(first_status["used_cache"])

            calls.clear()
            _, cached_status, _, cached_count = fetch_etf_flow.obtain_html(
                output_dir,
                False,
                self.NOW + timedelta(minutes=14),
                fetcher=fake_fetcher,
            )
            self.assertEqual(cached_count, 0)
            self.assertEqual(calls, [])
            self.assertTrue(cached_status["used_cache"])

            calls.clear()
            _, expired_status, _, expired_count = fetch_etf_flow.obtain_html(
                output_dir,
                False,
                self.NOW + timedelta(minutes=15),
                fetcher=fake_fetcher,
            )
            self.assertEqual(expired_count, 1)
            self.assertEqual(len(calls), 1)
            self.assertFalse(expired_status["used_cache"])

    def test_no_complete_row_reports_no_reliable_complete_value(self):
        provisional = self.make_row(
            "31 Jul 2026",
            "49.7",
            IBIT="-",
            FBTC="49.7",
        )
        parsed = fetch_etf_flow.parse_farside_html(
            self.make_html([provisional])
        )

        with self.assertRaises(fetch_etf_flow.EtfFlowError) as raised:
            self.build(parsed)
        self.assertIn("未取得可靠完整值", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
