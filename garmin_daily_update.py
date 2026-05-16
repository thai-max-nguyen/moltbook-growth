#!/usr/bin/env python3
"""Daily Garmin pull — updates Health Profile + Athlete Plan baseline in Obsidian vault."""
import json, os, datetime, warnings, re
warnings.filterwarnings('ignore')

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box

import garth
from garth.auth_tokens import OAuth1Token, OAuth2Token
from garminconnect import Garmin

TOKEN_DIR  = os.path.expanduser("~/.openclaw/garmin_tokens")
VAULT      = os.path.expanduser("~/Documents/Claude Second Brain")
PROFILE_MD = f"{VAULT}/02 - User Profile/Max - Health Profile.md"
PLAN_MD    = f"{VAULT}/03 - Projects/max-hybrid-athlete-plan.md"
LOG_FILE   = os.path.expanduser("~/Library/Logs/mundo-bot/garmin_update.log")

console = Console()

def log(msg, level="info"):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    with open(LOG_FILE, "a") as f:
        f.write(f"[{ts}] {msg}\n")
    styles = {"info": "cyan", "ok": "green", "warn": "yellow", "error": "red bold"}
    icons  = {"info": "·", "ok": "✓", "warn": "⚠", "error": "✗"}
    style  = styles.get(level, "white")
    icon   = icons.get(level, "·")
    console.log(f"[{style}]{icon}[/{style}] {msg}")

def connect():
    with open(f"{TOKEN_DIR}/oauth1_token.json") as f:
        garth.configure(
            oauth1_token=OAuth1Token(**json.load(f)),
            oauth2_token=OAuth2Token(**json.load(open(f"{TOKEN_DIR}/oauth2_token.json")))
        )
    client = Garmin()
    client.garth = garth.client
    return client

def pull_data(client):
    today = datetime.date.today()
    yesterday = today - datetime.timedelta(days=1)
    week_ago  = today - datetime.timedelta(days=7)

    data = {"date": today.isoformat(), "yesterday": yesterday.isoformat()}

    # Recent activities (7 days)
    with console.status("[cyan]Fetching activities (7d)…[/cyan]"):
        try:
            acts = client.get_activities_by_date(week_ago.isoformat(), today.isoformat())
            data["activities"] = [{
                "date": a.get("startTimeLocal","")[:10],
                "type": a.get("activityType",{}).get("typeKey",""),
                "distance_m": round(a.get("distance",0)),
                "duration_s": round(a.get("duration",0)),
                "avg_hr": a.get("averageHR"),
                "max_hr": a.get("maxHR"),
                "load": round(a.get("activityTrainingLoad",0),1),
            } for a in acts]
            log(f"activities: {len(data['activities'])} in last 7d", "ok")
        except Exception as e:
            log(f"activities error: {e}", "error")
            data["activities"] = []

    # Garmin records sleep under the date sleep ENDED (i.e. the morning the user woke up).
    # Querying `today` returns last night's sleep — querying `yesterday` would be the night before.
    with console.status("[cyan]Fetching sleep…[/cyan]"):
        try:
            s = client.get_sleep_data(today.isoformat())
            sd = s.get("dailySleepDTO", {})
            score = sd.get("sleepScores",{})
            total_s = sd.get("sleepTimeSeconds") or 0
            if total_s == 0:
                # fall back to most recent prior day with data (handles "ran before bedtime" pulls)
                fallback_date = (today - datetime.timedelta(days=1)).isoformat()
                s = client.get_sleep_data(fallback_date)
                sd = s.get("dailySleepDTO", {})
                score = sd.get("sleepScores",{})
                total_s = sd.get("sleepTimeSeconds") or 0
                use_date = fallback_date
            else:
                use_date = today.isoformat()
            if total_s == 0:
                log("no sleep recorded yet (Garmin record under wake-up date)", "warn")
                data["sleep"] = {}
            else:
                data["sleep"] = {
                    "date": use_date,
                    "total_h": round(total_s/3600, 2),
                    "deep_h":  round((sd.get("deepSleepSeconds") or 0)/3600, 2),
                    "rem_h":   round((sd.get("remSleepSeconds") or 0)/3600, 2),
                    "awake_m": round((sd.get("awakeSleepSeconds") or 0)/60, 1),
                    "score":   score.get("overall",{}).get("value") if isinstance(score, dict) else None,
                }
                log(f"sleep: {data['sleep']['total_h']}h · deep {data['sleep']['deep_h']}h · score {data['sleep']['score']} (date={use_date})", "ok")
        except Exception as e:
            log(f"sleep error: {e}", "error")
            data["sleep"] = {}

    with console.status("[cyan]Fetching stress…[/cyan]"):
        try:
            st = client.get_stress_data(yesterday.isoformat())
            vals = [x[1] for x in st.get("stressValuesArray",[]) if x[1] and x[1]>0]
            data["stress"] = {"avg": round(sum(vals)/len(vals)) if vals else None, "max": max(vals) if vals else None}
            log(f"stress: avg={data['stress']['avg']} max={data['stress']['max']}", "ok")
        except Exception as e:
            log(f"stress error: {e}", "error")
            data["stress"] = {}

    with console.status("[cyan]Fetching 30d running metrics…[/cyan]"):
        try:
            acts_30 = client.get_activities_by_date(
                (today - datetime.timedelta(days=30)).isoformat(), today.isoformat()
            )
            runs = [a for a in acts_30 if a.get("activityType",{}).get("typeKey","") == "running"]
            if runs:
                short_runs = [r for r in runs if 4000 <= r.get("distance",0) <= 6000 and r.get("averageHR",0) > 0]
                if short_runs:
                    best = min(short_runs, key=lambda r: r.get("duration",99999)/max(r.get("distance",1),1))
                    pace_s = best["duration"] / (best["distance"]/1000)
                    data["best_5k"] = {
                        "date": best.get("startTimeLocal","")[:10],
                        "pace_s_per_km": round(pace_s),
                        "pace_fmt": f"{int(pace_s//60)}:{int(pace_s%60):02d}/km",
                        "avg_hr": best.get("averageHR"),
                        "distance_m": round(best.get("distance",0))
                    }
                longest = max(runs, key=lambda r: r.get("distance",0))
                pace_s = longest["duration"] / max(longest.get("distance",1)/1000, 0.1)
                data["longest_run"] = {
                    "date": longest.get("startTimeLocal","")[:10],
                    "distance_km": round(longest.get("distance",0)/1000, 1),
                    "pace_fmt": f"{int(pace_s//60)}:{int(pace_s%60):02d}/km",
                    "avg_hr": longest.get("averageHR"),
                }
                log(f"best 5k: {data.get('best_5k',{}).get('pace_fmt')} | longest: {data.get('longest_run',{}).get('distance_km')}km", "ok")
        except Exception as e:
            log(f"performance metrics error: {e}", "error")

    return data

def format_baseline_section(data):
    today = data["date"]
    sleep = data.get("sleep", {})
    stress = data.get("stress", {})
    best_5k = data.get("best_5k", {})
    longest = data.get("longest_run", {})

    # Recent activity summary
    acts = data.get("activities", [])
    runs_7d = [a for a in acts if a["type"] == "running"]
    total_km_7d = round(sum(a["distance_m"] for a in runs_7d)/1000, 1)
    sessions_7d = len(acts)

    lines = [
        f"## CURRENT PERFORMANCE BASELINE",
        f"> Last pulled: {today}. Updated automatically each morning.",
        "",
        "| Metric | Value | Date | Trend |",
        "|---|---|---|---|",
    ]

    if best_5k:
        lines.append(f"| **5km pace (best/30d)** | {best_5k['pace_fmt']} @ HR {best_5k['avg_hr']} | {best_5k['date']} | — |")
    if longest:
        lines.append(f"| **Long run (best/30d)** | {longest['distance_km']}km @ {longest['pace_fmt']} @ HR {longest['avg_hr']} | {longest['date']} | — |")

    lines.append(f"| **Weekly km (7d)** | {total_km_7d} km ({len(runs_7d)} runs, {sessions_7d} sessions total) | {today} | — |")

    if sleep:
        score_str = str(sleep.get('score','—'))
        lines.append(f"| **Sleep (last night)** | {sleep.get('total_h')}h total · {sleep.get('deep_h')}h deep · score {score_str} | {sleep.get('date')} | — |")

    if stress.get("avg"):
        lines.append(f"| **Stress (yesterday)** | avg {stress['avg']} · max {stress['max']} | {data['yesterday']} | — |")

    lines += [
        "| **Max HR recorded** | 177 bpm | Apr 11 | — |",
        "| **Running cadence** | 136 spm avg | — | target 170+ |",
        "| **VO2Max** | null (Garmin not recording) | — | — |",
        "",
        "**HR Zones (max HR 177):**",
        "",
        "| Zone | HR | Pace equiv | When |",
        "|---|---|---|---|",
        "| Z1 Recovery | <106 | >10:30/km | walks, cooldown |",
        "| Z2 Aerobic | 106–124 | 8:30–9:30/km | easy runs |",
        "| Z3 Long run | 124–142 | 7:30–8:15/km | long runs |",
        "| Z4 Threshold | 142–159 | 6:45–7:30/km | tempo |",
        "| Z5 Max | 159–177 | <6:45/km | intervals, HIIT |",
    ]
    return "\n".join(lines)

def update_plan_baseline(new_section):
    with open(PLAN_MD) as f:
        content = f.read()

    # Replace section between CURRENT PERFORMANCE BASELINE and the next ---
    pattern = r"## CURRENT PERFORMANCE BASELINE.*?(?=\n---\n)"
    replacement = new_section
    new_content = re.sub(pattern, replacement, content, flags=re.DOTALL)

    if new_content != content:
        with open(PLAN_MD, "w") as f:
            f.write(new_content)
        log("plan baseline updated")
    else:
        log("plan: no change detected")

def append_to_health_profile(data):
    """Append today's activity summary to Health Profile as a new daily log entry."""
    sleep = data.get("sleep", {})
    stress = data.get("stress", {})
    acts = data.get("activities", [])
    today_acts = [a for a in acts if a["date"] == data["yesterday"]]

    if not today_acts and not sleep:
        log("no new data for health profile append")
        return

    with open(PROFILE_MD) as f:
        content = f.read()

    entry_lines = [f"\n### {data['yesterday']}"]
    if sleep:
        entry_lines.append(f"- Sleep: {sleep.get('total_h')}h · deep {sleep.get('deep_h')}h · REM {sleep.get('rem_h')}h · awake {sleep.get('awake_m')}m · score {sleep.get('score','—')}")
    if stress.get("avg"):
        entry_lines.append(f"- Stress: avg {stress['avg']} / max {stress['max']}")
    for a in today_acts:
        dist = f" {round(a['distance_m']/1000,1)}km" if a['distance_m'] > 100 else ""
        dur = f" {round(a['duration_s']/60)}min"
        hr = f" HR {a['avg_hr']}/{a['max_hr']}" if a['avg_hr'] else ""
        entry_lines.append(f"- {a['type']}{dist}{dur}{hr} load={a['load']}")

    # Append after "## Daily Log" section or at end
    marker = "## Daily Log"
    if marker in content:
        insert_pos = content.index(marker) + len(marker)
        content = content[:insert_pos] + "\n".join(entry_lines) + content[insert_pos:]
    else:
        content += "\n\n## Daily Log" + "\n".join(entry_lines)

    with open(PROFILE_MD, "w") as f:
        f.write(content)
    log(f"health profile updated with {len(today_acts)} activities")

def print_summary(data):
    t = Table(title=f"Garmin Pull — {data['date']}", box=box.ROUNDED, border_style="cyan")
    t.add_column("Metric", style="bold white", no_wrap=True)
    t.add_column("Value", style="green")

    sleep = data.get("sleep", {})
    stress = data.get("stress", {})
    best_5k = data.get("best_5k", {})
    longest = data.get("longest_run", {})
    acts = data.get("activities", [])
    runs_7d = [a for a in acts if a["type"] == "running"]

    if sleep:
        t.add_row("Sleep", f"{sleep.get('total_h')}h total · {sleep.get('deep_h')}h deep · score {sleep.get('score','—')}")
    if stress.get("avg"):
        stress_color = "green" if stress['avg'] < 25 else "yellow" if stress['avg'] < 40 else "red"
        t.add_row("Stress", f"[{stress_color}]avg {stress['avg']} · max {stress['max']}[/{stress_color}]")
    if best_5k:
        t.add_row("Best 5km pace", f"{best_5k.get('pace_fmt')} @ HR {best_5k.get('avg_hr')} ({best_5k.get('date')})")
    if longest:
        t.add_row("Longest run", f"{longest.get('distance_km')}km @ {longest.get('pace_fmt')} @ HR {longest.get('avg_hr')}")
    t.add_row("Weekly km", f"{round(sum(a['distance_m'] for a in runs_7d)/1000,1)} km · {len(runs_7d)} runs · {len(acts)} sessions")

    console.print(t)

def main():
    console.print(Panel("[bold cyan]Garmin Daily Update[/bold cyan]", border_style="cyan", expand=False))
    log("=== garmin daily update start ===")
    try:
        with console.status("[cyan]Connecting to Garmin…[/cyan]"):
            client = connect()
        log("connected", "ok")
        data = pull_data(client)
        # Vault mirror writes are SECONDARY — a macOS TCC PermissionError
        # (cron python lacks Full Disk Access to ~/Documents) must NOT abort
        # the run after Garmin data is already pulled. Degrade, don't FATAL.
        for label, fn in (
            ("plan baseline", lambda: update_plan_baseline(format_baseline_section(data))),
            ("health profile", lambda: append_to_health_profile(data)),
        ):
            try:
                with console.status(f"[cyan]Updating {label}…[/cyan]"):
                    fn()
            except (PermissionError, OSError) as ve:
                log(f"VAULT WRITE BLOCKED ({label}): {ve} — likely macOS TCC: "
                    f"grant Full Disk Access to /usr/bin/python3 (or cron), "
                    f"or rerun interactively. Garmin data pulled OK; continuing.",
                    "error")
        print_summary(data)
        log("=== done (garmin pull ok) ===", "ok")
    except Exception as e:
        log(f"FATAL: {e}", "error")
        raise

if __name__ == "__main__":
    main()
