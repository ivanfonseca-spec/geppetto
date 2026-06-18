"""
PHASE 3: FILE STORAGE
=====================
Handles saving and retrieving meeting transcripts and reports.

Stores in: Gepeto - Ivan/meetings/
Structure:
  meetings/
  ├── 2025-06-15_14-30_release_readiness/
  │   ├── transcript.txt
  │   ├── report.json
  │   └── report.html
  ├── 2025-06-15_15-45_planning_session/
  │   ├── transcript.txt
  │   ├── report.json
  │   └── report.html
"""

import os
import json
from datetime import datetime
from pathlib import Path


class MeetingStorage:
    """Manages saving and loading meeting transcripts and reports."""

    def __init__(self, base_dir="meetings"):
        """Initialize storage with base directory."""
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(exist_ok=True)

    def save_meeting(self, transcript, report, meeting_name=None):
        """
        Save a meeting transcript and report.

        Args:
            transcript (str): Meeting transcript
            report (dict): Validation report
            meeting_name (str): Optional custom meeting name

        Returns:
            dict: Saved meeting info (path, files, etc.)
        """
        # Generate meeting folder name
        if meeting_name:
            folder_name = self._sanitize_filename(meeting_name)
        else:
            now = datetime.now()
            folder_name = now.strftime("%Y-%m-%d_%H-%M-%S")

        meeting_dir = self.base_dir / folder_name
        meeting_dir.mkdir(exist_ok=True)

        # Save transcript
        transcript_file = meeting_dir / "transcript.txt"
        with open(transcript_file, 'w', encoding='utf-8') as f:
            f.write(transcript)

        # Save report as JSON
        report_json_file = meeting_dir / "report.json"
        with open(report_json_file, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2)

        # Save report as HTML (for viewing/printing)
        report_html_file = meeting_dir / "report.html"
        html = self._generate_html_report(report, transcript)
        with open(report_html_file, 'w', encoding='utf-8') as f:
            f.write(html)

        print(f"✅ Meeting saved: {meeting_dir}")
        print(f"   Transcript: {transcript_file}")
        print(f"   Report JSON: {report_json_file}")
        print(f"   Report HTML: {report_html_file}")

        return {
            "folder": str(meeting_dir),
            "transcript": str(transcript_file),
            "report_json": str(report_json_file),
            "report_html": str(report_html_file),
            "timestamp": datetime.now().isoformat(),
        }

    def list_meetings(self):
        """
        List all saved meetings.

        Returns:
            list: Meeting metadata (folder name, timestamp, file sizes)
        """
        meetings = []

        for meeting_dir in sorted(self.base_dir.iterdir(), reverse=True):
            if not meeting_dir.is_dir():
                continue

            transcript_file = meeting_dir / "transcript.txt"
            report_file = meeting_dir / "report.json"

            if transcript_file.exists() and report_file.exists():
                # Load report to get summary
                try:
                    with open(report_file, 'r', encoding='utf-8') as f:
                        report = json.load(f)
                    summary = report.get('summary', {})
                except:
                    summary = {}

                meetings.append({
                    "name": meeting_dir.name,
                    "path": str(meeting_dir),
                    "transcript_path": str(transcript_file),
                    "report_path": str(report_file),
                    "html_path": str(meeting_dir / "report.html"),
                    "total_claims": summary.get('total_claims', 0),
                    "verified": summary.get('verified', 0),
                    "contradicted": summary.get('contradicted', 0),
                    "unverified": summary.get('unverified', 0),
                    "critical_issues": summary.get('critical_issues', 0),
                })

        return meetings

    def load_meeting(self, folder_name):
        """
        Load a previously saved meeting.

        Args:
            folder_name (str): Name of the meeting folder

        Returns:
            dict: Transcript and report
        """
        meeting_dir = self.base_dir / folder_name
        transcript_file = meeting_dir / "transcript.txt"
        report_file = meeting_dir / "report.json"

        if not transcript_file.exists() or not report_file.exists():
            raise FileNotFoundError(f"Meeting {folder_name} not found")

        with open(transcript_file, 'r', encoding='utf-8') as f:
            transcript = f.read()

        with open(report_file, 'r', encoding='utf-8') as f:
            report = json.load(f)

        return {"transcript": transcript, "report": report}

    def _sanitize_filename(self, name):
        """Remove invalid filename characters."""
        return "".join(c for c in name if c.isalnum() or c in (' ', '-', '_')).rstrip()

    def _generate_html_report(self, report, transcript):
        """Generate HTML version of report."""
        summary = report['summary']
        action_items = report['action_items']
        validations = report['validations']

        category_icons = {
            "VERIFIED": "🟢",
            "CONTRADICTED": "🔴",
            "UNVERIFIED": "🟡",
            "OUTDATED": "⏰",
            "NEEDS_CLARIFICATION": "❓"
        }

        html = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Meeting Report - {summary.get('timestamp', 'Unknown Date')}</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            line-height: 1.6;
            color: #333;
            max-width: 1000px;
            margin: 0 auto;
            padding: 20px;
            background: #f9f9f9;
        }}

        header {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 30px;
            border-radius: 10px;
            margin-bottom: 30px;
        }}

        h1, h2 {{
            color: #333;
        }}

        header h1 {{
            color: white;
            margin: 0 0 10px 0;
        }}

        .timestamp {{
            color: rgba(255, 255, 255, 0.8);
            font-size: 0.9em;
        }}

        .summary-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
            gap: 15px;
            margin: 20px 0;
        }}

        .stat-card {{
            background: white;
            padding: 15px;
            border-radius: 8px;
            text-align: center;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}

        .stat-number {{
            font-size: 2em;
            font-weight: bold;
            color: #667eea;
        }}

        .stat-label {{
            color: #666;
            font-size: 0.9em;
            margin-top: 5px;
        }}

        .action-item {{
            background: #fff3cd;
            border-left: 4px solid #ffc107;
            padding: 15px;
            margin: 10px 0;
            border-radius: 4px;
        }}

        .action-item.critical {{
            background: #f8d7da;
            border-left-color: #dc3545;
        }}

        .action-item.high {{
            background: #fff3cd;
            border-left-color: #ffc107;
        }}

        .validation {{
            background: white;
            padding: 15px;
            margin: 10px 0;
            border-left: 4px solid #ccc;
            border-radius: 4px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.05);
        }}

        .validation.verified {{ border-left-color: #28a745; }}
        .validation.contradicted {{ border-left-color: #dc3545; }}
        .validation.unverified {{ border-left-color: #ffc107; }}
        .validation.outdated {{ border-left-color: #6c757d; }}
        .validation.needs-clarification {{ border-left-color: #17a2b8; }}

        .transcript-box {{
            background: white;
            padding: 15px;
            border-radius: 8px;
            border: 1px solid #e0e0e0;
            font-family: 'Courier New', monospace;
            white-space: pre-wrap;
            word-wrap: break-word;
            font-size: 0.9em;
            max-height: 400px;
            overflow-y: auto;
        }}

        .section {{
            background: white;
            padding: 20px;
            border-radius: 8px;
            margin-bottom: 20px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.05);
        }}

        @media print {{
            body {{ background: white; }}
            .section {{ box-shadow: none; border: 1px solid #e0e0e0; }}
        }}
    </style>
</head>
<body>
    <header>
        <h1>📊 Meeting Truth Layer Report</h1>
        <p class="timestamp">Generated: {summary.get('timestamp', 'Unknown')}</p>
    </header>

    <div class="section">
        <h2>Summary</h2>
        <div class="summary-grid">
            <div class="stat-card">
                <div class="stat-number">{summary.get('total_claims', 0)}</div>
                <div class="stat-label">Total Claims</div>
            </div>
            <div class="stat-card">
                <div class="stat-number">🟢 {summary.get('verified', 0)}</div>
                <div class="stat-label">Verified</div>
            </div>
            <div class="stat-card">
                <div class="stat-number">🔴 {summary.get('contradicted', 0)}</div>
                <div class="stat-label">Contradicted</div>
            </div>
            <div class="stat-card">
                <div class="stat-number">🟡 {summary.get('unverified', 0)}</div>
                <div class="stat-label">Unverified</div>
            </div>
            <div class="stat-card">
                <div class="stat-number">⏰ {summary.get('outdated', 0)}</div>
                <div class="stat-label">Outdated</div>
            </div>
            <div class="stat-card">
                <div class="stat-number">❓ {summary.get('needs_clarification', 0)}</div>
                <div class="stat-label">Need Clarification</div>
            </div>
        </div>
    </div>

    <div class="section">
        <h2>🎯 Action Items</h2>
"""

        if action_items:
            for item in action_items:
                css_class = f"action-item {item['priority'].lower()}"
                html += f"""
        <div class="{css_class}">
            <strong>[{item['priority']}]</strong> {item['action']}<br>
            <small><em>"{item['claim']}"</em><br>
            {item['reasoning']}</small>
        </div>
"""
        else:
            html += "<p>No action items.</p>"

        html += """
    </div>

    <div class="section">
        <h2>📋 All Validations</h2>
"""

        for v in validations:
            icon = category_icons.get(v['category'], '?')
            css_class = f"validation {v['category'].lower().replace('_', '-')}"
            html += f"""
        <div class="{css_class}">
            <strong>{icon} {v['category']}</strong> ({v['confidence']:.0%} confidence)<br>
            <em>"{v['claim']}"</em><br>
            <small>{v['reasoning']}</small>
        </div>
"""

        html += f"""
    </div>

    <div class="section">
        <h2>📝 Original Transcript</h2>
        <div class="transcript-box">{transcript}</div>
    </div>

    <footer style="text-align: center; color: #999; margin-top: 40px; padding: 20px;">
        <p>Meeting Truth Layer | Report generated {summary.get('timestamp', 'Unknown')}</p>
    </footer>
</body>
</html>
"""
        return html


# Singleton instance
_storage = None

def get_storage():
    """Get or create the storage instance."""
    global _storage
    if _storage is None:
        _storage = MeetingStorage()
    return _storage
