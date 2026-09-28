#!/usr/bin/env python3
"""按 cron_jobs.example.json 在新机器重建定时任务（调用 hermes cron create）。"""
import argparse, json, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))


def sched_str(job):
    s = job.get("schedule") or {}
    if isinstance(s, str):
        return s
    if s.get("kind") == "interval":
        return f"every {s.get('minutes') or 60}m"
    return s.get("expr") or s.get("display") or "0 13 * * *"


def build(job, deliver):
    cmd = ["hermes", "cron", "create", sched_str(job)]
    if job.get("prompt"):
        cmd += [job["prompt"]]
    cmd += ["--name", job["name"], "--deliver", deliver or job.get("deliver", "local")]
    skills = job.get("skills") or ([job["skill"]] if job.get("skill") else [])
    for s in skills:
        cmd += ["--skill", s]
    if job.get("script"):
        cmd += ["--script", job["script"]]
    if job.get("no_agent"):
        cmd += ["--no-agent"]
    if job.get("model"):
        cmd += ["--model", job["model"]]
    if job.get("provider"):
        cmd += ["--provider", job["provider"]]
    return cmd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", default=os.path.join(HERE, "cron_jobs.example.json"),
                    help="cron job definitions (default: bundled example)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--deliver", default=None, help="统一投递目标，如 weixin:<chat_id> 或 local")
    a = ap.parse_args()
    with open(a.jobs, encoding="utf-8") as f:
        jobs = json.load(f)
    for job in jobs:
        cmd = build(job, a.deliver)
        print("+", " ".join(x if len(x) < 70 else x[:67] + "..." for x in cmd))
        if not a.dry_run:
            r = subprocess.run(cmd, capture_output=True, text=True)
            print("   ", (r.stdout or r.stderr).strip().splitlines()[:2])
    return 0


if __name__ == "__main__":
    sys.exit(main())
