"""
PHASE 2: KNOWLEDGE BASE SETUP
==============================
Creates a ChromaDB knowledge base from sample project documents.
This simulates having real project documents (SOW, specs, decisions, etc.)

Usage:
  python phase2_kb_setup.py

Creates:
  - ChromaDB collection with indexed documents
  - Embeddings for semantic search
  - Ready for Phase 2 validation engine
"""

import os
import chromadb

# Document as_of dates — used for KB metadata (Geppetto 3)
DOC_DATES = {
    "SOW_v1.md":                    "2026-06-01",
    "architecture_decision_log.md": "2026-05-01",
    "qa_status_report.md":          "2026-06-14",
    "product_decisions.md":         "2026-06-10",
    "meeting_transcript_2025_06_10.txt": "2026-06-10",
    "approved_features_v1.md":      "2026-06-10",
}

# ============================================================================
# SAMPLE PROJECT DOCUMENTS
# ============================================================================
# These simulate a real project: a SaaS product release

SAMPLE_DOCUMENTS = {
    "SOW_v1.md": """
# Statement of Work - Project Alpha Release v1.0
Date: 2025-06-01
Status: APPROVED

## Deliverables
- Backend API (REST)
- Mobile app (iOS + Android)
- Dashboard UI
- Documentation

## Timeline
- QA: June 1-15 (82% of tests passed as of June 14; see QA status report)
- UAT: June 16-20
- Production Release: June 21

## Budget
- Total: $500,000
- Spent: $420,000
- Remaining: $80,000

## Team
- Engineering: 12 people
- QA: 3 people
- Product: 2 people

## Risks
- Database migration still pending
- Mobile app store review process (1-2 weeks)
""",

    "architecture_decision_log.md": """
# Architecture Decision Log

## ADR-001: Use PostgreSQL for main database
Date: 2025-04-15
Status: APPROVED
Author: Engineering Lead

Decision: Use PostgreSQL 14+ with pgvector for embeddings support.
Rationale: Mature, reliable, good vector DB support, team expertise.

## ADR-002: Migrate from MySQL to PostgreSQL
Date: 2025-05-01
Status: IN PROGRESS
Timeline: Complete by June 12

Migration includes:
- Schema redesign
- Data export from MySQL
- Testing on staging
- Production cutover

## ADR-003: Use REST API (not GraphQL)
Date: 2025-03-01
Status: APPROVED
Decision: REST for v1.0. GraphQL considered for v2.0.

## ADR-004: Authentication via OAuth2
Date: 2025-03-15
Status: APPROVED
Implemented: Google, GitHub, Microsoft providers

## ADR-005: API rate limiting
Date: 2025-05-20
Status: APPROVED
Limits: 1000 requests/hour per user
Status: Not yet enforced (planned for Phase 2)
""",

    "qa_status_report.md": """
# QA Status Report - Week of June 9-13

## Test Execution Summary
- Total Tests: 450
- Passed: 368 (82%)
- Failed: 50 (11%)
- Blocked: 32 (7%)

## Coverage by Module
- Authentication: 100% (45/45 passed)
- API Endpoints: 85% (120/141 passed)
- Mobile (iOS): 78% (95/122 passed)
- Mobile (Android): 75% (88/118 passed)
- Dashboard: 80% (20/25 passed)

## Critical Issues
- None (all blockers resolved)

## Known Issues
- Mobile app crashes on Android 10 devices (5% of users) - MEDIUM
- Dashboard performance issue with >10K records - LOW
- API rate limiting not enforced - LOW

## Next Steps
- Complete remaining 82 failing tests
- Performance optimization for dashboard
- Mobile crash investigation
- UAT readiness assessment

## Estimated Completion
- QA sign-off: June 15 (current on schedule)
""",

    "product_decisions.md": """
# Product Decisions Log

## Decision: Feature Parity Release
Date: 2025-05-15
Approved by: Product Lead
Status: APPROVED

v1.0 will include all feature parity with legacy system.
No new features in v1.0 (focus on stability and QA).

## Decision: Mobile-First on Android
Date: 2025-04-20
Approved by: CEO
Status: APPROVED

Android launch first (larger market), iOS follows 1 week later.
Rationale: Time to market, market size.

## Decision: Pricing Model
Date: 2025-06-01
Approved by: CEO + CFO
Status: APPROVED

- Free tier: up to 1000 API calls/month
- Pro: $99/month (10K calls)
- Enterprise: Custom pricing
- First 100 customers: 50% discount (3 months)

## Decision: Go-Live Scope
Date: 2025-06-10
Approved by: Product Lead, Engineering Lead, CEO
Status: APPROVED

Go-live includes:
✓ Authentication
✓ Core API endpoints
✓ Dashboard
✓ Mobile apps
✓ Onboarding

Does NOT include (Phase 2):
✗ Advanced analytics
✗ Custom integrations
✗ GraphQL API
✗ CLI tool
""",

    "meeting_transcript_2025_06_10.txt": """
MEETING TRANSCRIPT: Release Readiness Review
Date: June 10, 2025
Attendees: CEO, Product Lead, Engineering Lead, QA Lead

CEO: "Where are we on the June 21 release?"

Engineering Lead: "Backend is production-ready. Database migration is on track for June 12. Mobile apps are in testing."

QA Lead: "We're at 82% test coverage. Critical issues resolved. Expecting QA sign-off by June 15."

Product Lead: "All features for v1.0 are in scope. Marketing is ready for June 21 launch."

CEO: "Any blockers?"

Engineering Lead: "Mobile app store review is wild card. Could take 1-2 weeks after submission."

QA Lead: "No blockers on our side. Tests are progressing normally."

Product Lead: "Timeline is tight but achievable."

CEO: "Let's commit to June 21. I'll handle app store escalation if needed."
""",

    "approved_features_v1.md": """
# Approved Features for v1.0

## Core Features
1. User Authentication (OAuth2)
   - Google login
   - GitHub login
   - Microsoft login
   - Status: COMPLETE

2. Dashboard
   - User profile
   - Data visualization
   - Settings
   - Status: IN TESTING (80% complete)

3. API Endpoints
   - /api/users
   - /api/data
   - /api/reports
   - Status: IN TESTING (85% complete)

4. Mobile Apps
   - iOS app
   - Android app
   - Push notifications
   - Status: IN TESTING (75-78% complete)

5. Documentation
   - API docs (OpenAPI/Swagger)
   - User guide
   - Admin guide
   - Status: 90% complete

## Performance Requirements
- API response time: < 200ms p95
- Dashboard load time: < 2 seconds
- Mobile app cold start: < 3 seconds

## Security Requirements
- All data encrypted in transit (TLS 1.3)
- OAuth2 for authentication
- Rate limiting (planned for Phase 2)
- GDPR compliance

## Browser/Device Support
- Chrome 90+
- Firefox 88+
- Safari 14+
- iOS 13+
- Android 10+
""",
}

# ============================================================================
# CREATE CHROMADB COLLECTION AND INDEX DOCUMENTS
# ============================================================================

def setup_knowledge_base():
    """
    Create ChromaDB collection and index all sample documents.
    """
    print("\n" + "="*70)
    print("SETTING UP KNOWLEDGE BASE")
    print("="*70)

    # Initialize ChromaDB with new API (stores in ./chroma_data directory)
    client = chromadb.PersistentClient(path="./chroma_data")

    # Delete existing collection if it exists (start fresh)
    try:
        client.delete_collection(name="project_knowledge")
    except:
        pass

    # Create new collection
    collection = client.create_collection(
        name="project_knowledge",
        metadata={"hnsw:space": "cosine"}
    )

    print(f"\n✓ Created ChromaDB collection: 'project_knowledge'")

    # Index all documents
    doc_count = 0
    for doc_name, doc_content in SAMPLE_DOCUMENTS.items():
        # Split long documents into chunks (helpful for search)
        chunks = chunk_document(doc_content, chunk_size=500)

        for i, chunk in enumerate(chunks):
            doc_id = f"{doc_name}_chunk_{i}"

            collection.add(
                ids=[doc_id],
                documents=[chunk],
                metadatas=[{
                    "source": doc_name,
                    "chunk": i,
                    "type": get_doc_type(doc_name),
                    "as_of": DOC_DATES.get(doc_name, "2026-06-01"),
                }]
            )
            doc_count += 1

    print(f"✓ Indexed {len(SAMPLE_DOCUMENTS)} documents ({doc_count} chunks)")
    print(f"✓ Saved to ./chroma_data/ (auto-persisted)")

    return client, collection


def chunk_document(text, chunk_size=500):
    """
    Split document into chunks by line breaks, keeping them ~chunk_size chars.
    """
    lines = text.split('\n')
    chunks = []
    current_chunk = []
    current_size = 0

    for line in lines:
        if current_size + len(line) > chunk_size and current_chunk:
            chunks.append('\n'.join(current_chunk))
            current_chunk = [line]
            current_size = len(line)
        else:
            current_chunk.append(line)
            current_size += len(line)

    if current_chunk:
        chunks.append('\n'.join(current_chunk))

    return [c for c in chunks if c.strip()]  # Remove empty chunks


def get_doc_type(filename):
    """Categorize document by filename."""
    if 'sow' in filename.lower():
        return 'sow'
    elif 'architecture' in filename.lower():
        return 'architecture'
    elif 'qa' in filename.lower():
        return 'qa_status'
    elif 'product' in filename.lower():
        return 'product_decision'
    elif 'meeting' in filename.lower():
        return 'meeting_transcript'
    elif 'feature' in filename.lower():
        return 'feature_spec'
    else:
        return 'other'


def query_knowledge_base(collection, query_text, n_results=3):
    """
    Query the knowledge base for relevant documents.

    Args:
        collection: ChromaDB collection
        query_text: What to search for
        n_results: How many results to return

    Returns:
        List of (document, metadata) tuples
    """
    results = collection.query(
        query_texts=[query_text],
        n_results=n_results
    )

    if not results['documents'] or not results['documents'][0]:
        return []

    # Reformat results
    formatted = []
    for i, doc in enumerate(results['documents'][0]):
        metadata = results['metadatas'][0][i] if results['metadatas'] else {}
        formatted.append((doc, metadata))

    return formatted


# ============================================================================
# MAIN
# ============================================================================

def main():
    print("\n" + "█" * 70)
    print("█" + " " * 68 + "█")
    print("█" + "  PHASE 2: KNOWLEDGE BASE SETUP".center(68) + "█")
    print("█" + " " * 68 + "█")
    print("█" * 70)

    # Create KB
    client, collection = setup_knowledge_base()

    # Test queries
    print(f"\n{'='*70}")
    print("TESTING KNOWLEDGE BASE QUERIES")
    print(f"{'='*70}")

    test_queries = [
        "What is the QA status?",
        "When is the release date?",
        "What are the approved features?",
        "Has the database migration started?",
    ]

    for query in test_queries:
        print(f"\nQuery: '{query}'")
        results = query_knowledge_base(collection, query)
        if results:
            for i, (doc, metadata) in enumerate(results):
                print(f"  [{i+1}] From {metadata.get('source', 'unknown')}")
                print(f"      {doc[:100]}...")
        else:
            print("  No results found")

    print(f"\n{'='*70}")
    print("✅ KNOWLEDGE BASE SETUP COMPLETE!")
    print(f"{'='*70}")
    print("\nYour KB is ready for Phase 2 validation engine.")
    print("Next: phase2_validator.py")

    return client, collection


if __name__ == "__main__":
    main()
