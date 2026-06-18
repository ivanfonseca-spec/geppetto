"""
PHASE 3: INTEGRATION LAYER
==========================
Orchestrates Phase 1 (audio capture) + Phase 2 (validation)
to create a unified workflow.

This module provides clean interfaces for:
- Recording audio and transcribing
- Validating transcripts
- Generating structured reports
"""

import os
from phase2_kb_setup import setup_knowledge_base, query_knowledge_base
from phase2_validator import validate_transcript, get_priority
from datetime import datetime


class MeetingValidator:
    """Main orchestrator for the full validation pipeline."""

    def __init__(self):
        """Initialize with knowledge base."""
        self.kb_client = None
        self.kb_collection = None
        self._setup_kb()

    def _setup_kb(self):
        """Load or create knowledge base."""
        try:
            import chromadb
            self.kb_client = chromadb.PersistentClient(path="./chroma_data")
            self.kb_collection = self.kb_client.get_collection(
                name="project_knowledge"
            )
        except:
            # KB doesn't exist, create it
            print("Creating knowledge base...")
            self.kb_client, self.kb_collection = setup_knowledge_base()

    def validate_meeting(self, transcript):
        """
        Full validation pipeline for a meeting transcript.

        Args:
            transcript (str): Meeting transcript to validate

        Returns:
            dict: Structured validation report
        """
        # Step 1: Validate all claims
        validations = validate_transcript(transcript, self.kb_collection)

        # Step 2: Generate report
        report = self._generate_report(validations, transcript)

        return report

    def _generate_report(self, validations, transcript):
        """Generate structured report from validations."""

        # Count by category
        summary = {
            "timestamp": datetime.now().isoformat(),
            "total_claims": len(validations),
            "verified": len([v for v in validations if v['category'] == 'VERIFIED']),
            "contradicted": len([v for v in validations if v['category'] == 'CONTRADICTED']),
            "unverified": len([v for v in validations if v['category'] == 'UNVERIFIED']),
            "outdated": len([v for v in validations if v['category'] == 'OUTDATED']),
            "needs_clarification": len([v for v in validations if v['category'] == 'NEEDS_CLARIFICATION']),
            "critical_issues": len([v for v in validations if v['priority'] == 'CRITICAL']),
            "high_issues": len([v for v in validations if v['priority'] == 'HIGH']),
        }

        # Separate by priority
        critical = [v for v in validations if v['priority'] == 'CRITICAL']
        high = [v for v in validations if v['priority'] == 'HIGH']
        medium = [v for v in validations if v['priority'] == 'MEDIUM']

        # Action items
        action_items = []
        for item in critical + high + medium:
            action_items.append({
                "priority": item['priority'],
                "claim": item['claim'],
                "action": item['pm_action_suggested'],
                "reasoning": item['reasoning'],
                "category": item['category'],
                "confidence": item['confidence']
            })

        return {
            "summary": summary,
            "validations": validations,
            "action_items": action_items,
            "transcript": transcript,
        }

    def export_pdf(self, report):
        """
        Export report as PDF (future: implement with reportlab).
        For now, returns HTML that can be printed to PDF.
        """
        html = self._generate_html_report(report)
        return html

    def _generate_html_report(self, report):
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
<html>
<head>
    <title>Meeting Truth Layer - Report</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 20px; }}
        h1 {{ color: #333; }}
        .summary {{ background: #f5f5f5; padding: 15px; border-radius: 5px; }}
        .stat {{ display: inline-block; margin: 10px 20px 10px 0; }}
        .action-items {{ margin: 20px 0; }}
        .action {{ background: #fff3cd; padding: 10px; margin: 10px 0; border-left: 4px solid #ffc107; }}
        .critical {{ border-left-color: #dc3545; background: #f8d7da; }}
        .validation {{ margin: 10px 0; padding: 10px; border: 1px solid #ddd; }}
        .timestamp {{ color: #666; font-size: 0.9em; }}
    </style>
</head>
<body>
    <h1>📊 Meeting Truth Layer - Validation Report</h1>
    <p class="timestamp">Generated: {summary['timestamp']}</p>

    <div class="summary">
        <h2>Summary</h2>
        <div class="stat"><strong>{summary['total_claims']}</strong> claims analyzed</div>
        <div class="stat">🟢 <strong>{summary['verified']}</strong> verified</div>
        <div class="stat">🔴 <strong>{summary['contradicted']}</strong> contradicted</div>
        <div class="stat">🟡 <strong>{summary['unverified']}</strong> unverified</div>
        <div class="stat">⏰ <strong>{summary['outdated']}</strong> outdated</div>
        <div class="stat">❓ <strong>{summary['needs_clarification']}</strong> need clarification</div>
        <br>
        <div class="stat"><strong>{summary['critical_issues']}</strong> critical issues</div>
        <div class="stat"><strong>{summary['high_issues']}</strong> high priority issues</div>
    </div>

    <div class="action-items">
        <h2>🎯 Action Items</h2>
"""

        for item in action_items:
            css_class = "action"
            if item['priority'] == 'CRITICAL':
                css_class += " critical"
            html += f"""
        <div class="{css_class}">
            <strong>[{item['priority']}]</strong> {item['action']}<br>
            <small>Claim: "{item['claim']}"<br>
            Reason: {item['reasoning']}</small>
        </div>
"""

        html += """
    </div>

    <div>
        <h2>📋 All Validations</h2>
"""

        for v in validations:
            icon = category_icons.get(v['category'], '?')
            html += f"""
        <div class="validation">
            <strong>{icon} {v['category']}</strong> ({v['confidence']:.0%} confidence)<br>
            <em>"{v['claim']}"</em><br>
            <small>{v['reasoning']}</small>
        </div>
"""

        html += """
    </div>
</body>
</html>
"""
        return html


# Singleton instance
_validator = None

def get_validator():
    """Get or create the validator instance."""
    global _validator
    if _validator is None:
        _validator = MeetingValidator()
    return _validator
