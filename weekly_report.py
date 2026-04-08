"""
VGB Delta Bot v2 — Weekly Report & Analytics
===============================================
Generates weekly reports with:
- PnL summary (total, by session, by day)
- Win/loss analysis
- Best/worst trades
- Session performance comparison
- Health metrics
- Improvement suggestions
- Stores everything in CSV for journaling
"""

import csv
import os
from datetime import datetime, timedelta
from collections import defaultdict
import config
from session_manager import get_ist_now


JOURNAL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'journals')
WEEKLY_REPORT_FILE = os.path.join(JOURNAL_DIR, 'weekly_reports.csv')


def ensure_journal_dir():
    os.makedirs(JOURNAL_DIR, exist_ok=True)


def load_trades(days=7):
    """Load trades from trade_log.csv for the last N days."""
    trades = []
    if not os.path.exists(config.TRADE_LOG_FILE):
        return trades

    cutoff = get_ist_now() - timedelta(days=days)

    with open(config.TRADE_LOG_FILE, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                ts = datetime.strptime(row['timestamp'], '%Y-%m-%d %H:%M:%S')
                if ts >= cutoff:
                    row['_timestamp'] = ts
                    row['pnl'] = float(row.get('pnl', 0))
                    row['entry_price'] = float(row.get('entry_price', 0))
                    row['exit_price'] = float(row.get('exit_price', 0))
                    row['capital_after'] = float(row.get('capital_after', 0))
                    trades.append(row)
            except:
                continue
    return trades


def generate_weekly_report():
    """Generate comprehensive weekly report."""
    ensure_journal_dir()
    trades = load_trades(7)
    now = get_ist_now()
    week_start = (now - timedelta(days=7)).strftime('%Y-%m-%d')
    week_end = now.strftime('%Y-%m-%d')

    report = {
        'week': f"{week_start} to {week_end}",
        'generated_at': now.strftime('%Y-%m-%d %H:%M:%S'),
    }

    if not trades:
        report['status'] = 'NO TRADES THIS WEEK'
        _save_report(report)
        return report, _format_report(report)

    # Basic stats
    total_trades = len(trades)
    wins = [t for t in trades if t['pnl'] >= 0]
    losses = [t for t in trades if t['pnl'] < 0]
    total_pnl = sum(t['pnl'] for t in trades)
    win_rate = (len(wins) / total_trades * 100) if total_trades > 0 else 0

    report['total_trades'] = total_trades
    report['wins'] = len(wins)
    report['losses'] = len(losses)
    report['win_rate'] = round(win_rate, 1)
    report['total_pnl'] = round(total_pnl, 2)
    report['current_capital'] = round(trades[-1]['capital_after'], 2) if trades else 0

    # Best and worst trades
    if trades:
        best = max(trades, key=lambda t: t['pnl'])
        worst = min(trades, key=lambda t: t['pnl'])
        report['best_trade'] = f"{best['side']} ${best['entry_price']:.1f}→${best['exit_price']:.1f} PnL:${best['pnl']:+.2f} ({best['session']})"
        report['worst_trade'] = f"{worst['side']} ${worst['entry_price']:.1f}→${worst['exit_price']:.1f} PnL:${worst['pnl']:+.2f} ({worst['session']})"

    # Session breakdown
    session_stats = defaultdict(lambda: {'trades': 0, 'pnl': 0, 'wins': 0})
    for t in trades:
        sess = t.get('session', 'UNKNOWN')
        session_stats[sess]['trades'] += 1
        session_stats[sess]['pnl'] += t['pnl']
        if t['pnl'] >= 0:
            session_stats[sess]['wins'] += 1

    report['session_breakdown'] = {}
    for sess, stats in session_stats.items():
        wr = (stats['wins'] / stats['trades'] * 100) if stats['trades'] > 0 else 0
        report['session_breakdown'][sess] = {
            'trades': stats['trades'],
            'pnl': round(stats['pnl'], 2),
            'win_rate': round(wr, 1)
        }

    # Daily breakdown
    daily_stats = defaultdict(lambda: {'trades': 0, 'pnl': 0})
    for t in trades:
        day = t['_timestamp'].strftime('%Y-%m-%d (%a)')
        daily_stats[day]['trades'] += 1
        daily_stats[day]['pnl'] += t['pnl']

    report['daily_breakdown'] = {d: {'trades': s['trades'], 'pnl': round(s['pnl'], 2)}
                                  for d, s in sorted(daily_stats.items())}

    # Exit reason breakdown
    reason_stats = defaultdict(lambda: {'count': 0, 'pnl': 0})
    for t in trades:
        reason = t.get('reason', 'UNKNOWN')
        reason_stats[reason]['count'] += 1
        reason_stats[reason]['pnl'] += t['pnl']
    report['exit_reasons'] = {r: {'count': s['count'], 'pnl': round(s['pnl'], 2)}
                               for r, s in reason_stats.items()}

    # Improvement suggestions
    suggestions = []

    # Check if any session is consistently losing
    for sess, stats in report.get('session_breakdown', {}).items():
        if stats['pnl'] < 0 and stats['trades'] >= 5:
            suggestions.append(f"⚠️ {sess} session lost ${abs(stats['pnl']):.2f} this week ({stats['trades']} trades, {stats['win_rate']}% WR). Consider reducing position size or disabling.")

    # Check if win rate is below threshold
    if win_rate < 40 and total_trades >= 10:
        suggestions.append(f"⚠️ Overall win rate {win_rate:.1f}% is below 40%. Market may be choppy. Consider pausing or reducing leverage.")

    # Check if too many trades
    avg_daily = total_trades / 7
    if avg_daily > 20:
        suggestions.append(f"⚠️ Averaging {avg_daily:.0f} trades/day — may be overtrading. Consider tighter filters.")

    # Check if one big loss skewed results
    if losses:
        biggest_loss = min(t['pnl'] for t in trades)
        if abs(biggest_loss) > abs(total_pnl) * 0.5 and total_pnl < 0:
            suggestions.append(f"⚠️ One large loss (${biggest_loss:.2f}) accounted for >50% of weekly loss. Review that trade specifically.")

    # Positive suggestions
    if total_pnl > 0:
        suggestions.append(f"✅ Profitable week! +${total_pnl:.2f}. Strategy performing as expected.")

    best_session = max(report.get('session_breakdown', {}).items(), key=lambda x: x[1]['pnl'], default=None)
    if best_session:
        suggestions.append(f"💡 Best session: {best_session[0]} (+${best_session[1]['pnl']:.2f}). Consider increasing allocation here.")

    report['suggestions'] = suggestions

    # Save
    _save_report(report)

    # Format for display/Telegram
    formatted = _format_report(report)

    return report, formatted


def _format_report(report):
    """Format report as readable text for Telegram/display."""
    lines = []
    lines.append(f"📊 <b>WEEKLY REPORT</b>")
    lines.append(f"📅 {report.get('week', 'N/A')}")
    lines.append("")

    if report.get('status') == 'NO TRADES THIS WEEK':
        lines.append("No trades executed this week.")
        return '\n'.join(lines)

    lines.append(f"💰 <b>PnL: ${report.get('total_pnl', 0):+.2f}</b>")
    lines.append(f"📈 Trades: {report.get('total_trades', 0)} | W:{report.get('wins', 0)} L:{report.get('losses', 0)} | WR:{report.get('win_rate', 0)}%")
    lines.append(f"🏦 Capital: ${report.get('current_capital', 0):,.2f}")
    lines.append("")

    # Session breakdown
    lines.append("<b>Sessions:</b>")
    for sess, stats in report.get('session_breakdown', {}).items():
        emoji = "✅" if stats['pnl'] >= 0 else "❌"
        lines.append(f"  {emoji} {sess}: {stats['trades']}tr | ${stats['pnl']:+.2f} | WR:{stats['win_rate']}%")
    lines.append("")

    # Best/worst
    if report.get('best_trade'):
        lines.append(f"🏆 Best: {report['best_trade']}")
    if report.get('worst_trade'):
        lines.append(f"💀 Worst: {report['worst_trade']}")
    lines.append("")

    # Daily
    lines.append("<b>Daily:</b>")
    for day, stats in report.get('daily_breakdown', {}).items():
        emoji = "🟢" if stats['pnl'] >= 0 else "🔴"
        lines.append(f"  {emoji} {day}: {stats['trades']}tr ${stats['pnl']:+.2f}")
    lines.append("")

    # Suggestions
    if report.get('suggestions'):
        lines.append("<b>Analysis:</b>")
        for s in report['suggestions']:
            lines.append(f"  {s}")

    return '\n'.join(lines)


def _save_report(report):
    """Save report to CSV journal."""
    ensure_journal_dir()

    file_exists = os.path.exists(WEEKLY_REPORT_FILE)
    flat = {
        'week': report.get('week', ''),
        'generated_at': report.get('generated_at', ''),
        'total_trades': report.get('total_trades', 0),
        'wins': report.get('wins', 0),
        'losses': report.get('losses', 0),
        'win_rate': report.get('win_rate', 0),
        'total_pnl': report.get('total_pnl', 0),
        'current_capital': report.get('current_capital', 0),
        'best_trade': report.get('best_trade', ''),
        'worst_trade': report.get('worst_trade', ''),
        'suggestions': ' | '.join(report.get('suggestions', [])),
    }

    # Add session PnL columns
    for sess in ['ASIA', 'LONDON', 'NY']:
        sess_data = report.get('session_breakdown', {}).get(sess, {})
        flat[f'{sess}_trades'] = sess_data.get('trades', 0)
        flat[f'{sess}_pnl'] = sess_data.get('pnl', 0)
        flat[f'{sess}_wr'] = sess_data.get('win_rate', 0)

    with open(WEEKLY_REPORT_FILE, 'a', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=flat.keys())
        if not file_exists:
            writer.writeheader()
        writer.writerow(flat)


def generate_health_report():
    """Generate health/system report."""
    now = get_ist_now()

    try:
        from watchdog import Watchdog
        wd = Watchdog()
        status = wd.get_status()
    except:
        status = {}

    # Check log file size
    log_size_mb = 0
    if os.path.exists(config.LOG_FILE):
        log_size_mb = os.path.getsize(config.LOG_FILE) / (1024 * 1024)

    # Check trade log
    trade_count = 0
    if os.path.exists(config.TRADE_LOG_FILE):
        with open(config.TRADE_LOG_FILE, 'r') as f:
            trade_count = sum(1 for _ in f) - 1  # minus header

    lines = []
    lines.append(f"🔧 <b>HEALTH REPORT</b>")
    lines.append(f"📅 {now.strftime('%Y-%m-%d %H:%M')} IST")
    lines.append(f"⏱ Uptime: {status.get('uptime', 'N/A')}")
    lines.append(f"❌ Errors today: {status.get('errors_today', 'N/A')}")
    lines.append(f"📊 Total trades logged: {trade_count}")
    lines.append(f"💾 Log size: {log_size_mb:.1f}MB")
    lines.append(f"🔒 Safe mode: {'YES ⚠️' if status.get('safe_mode') else 'No ✅'}")
    lines.append(f"📡 Data stale: {'YES ⚠️' if status.get('data_stale') else 'No ✅'}")

    return '\n'.join(lines)