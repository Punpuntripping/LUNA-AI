# AI Chat / Conversational AI SaaS Applications — Research Report

> **Date**: 2026-03-13
> **Purpose**: Survey of successful AI chat applications — open-source and commercial — to inform Luna Legal AI architecture decisions.

---

## Summary Table

| # | Project | URL | Tech Stack | Stars | Streaming | Database | Highlights |
|---|---------|-----|-----------|-------|-----------|----------|------------|
| 1 | **Open WebUI** | [GitHub](https://github.com/open-webui/open-webui) | SvelteKit + FastAPI (Python) | 80k+ | Socket.IO + SSE | SQLite / PostgreSQL | Pipeline architecture, native Ollama, plugin system, RAG built-in |
| 2 | **LibreChat** | [GitHub](https://github.com/danny-avila/LibreChat) | React + Express (Node.js) | 25k+ | SSE | MongoDB + MeiliSearch + pgvector | MCP support, Artifacts, Agents, multi-provider, Code Interpreter |
| 3 | **LobeChat** | [GitHub](https://github.com/lobehub/lobe-chat) | Next.js + tRPC | 60k+ | SSE (AI SDK) | PostgreSQL + PGVector | Plugin market, CRDT sync, knowledge bases, TTS/STT, i18n |
| 4 | **Chatbot UI** | [GitHub](https://github.com/mckaywrigley/chatbot-ui) | Next.js + Supabase | 30k+ | SSE (AI SDK) | Supabase PostgreSQL | Supabase auth, clean ChatGPT clone, simple schema |
| 5 | **Vercel AI Chatbot** | [GitHub](https://github.com/supabase-community/vercel-ai-chatbot) | Next.js + Vercel AI SDK + Supabase | 5k+ | SSE (streamText) | Supabase PostgreSQL | Official template, multi-provider, AI Gateway |
| 6 | **assistant-ui** | [GitHub](https://github.com/assistant-ui/assistant-ui) | React (component library) | 4k+ | Provider-agnostic | N/A (library) | YC-backed, Radix-style composable primitives, artifacts |
| 7 | **Vstorm Full-Stack Template** | [GitHub](https://github.com/vstorm-co/full-stack-fastapi-nextjs-llm-template) | FastAPI + Next.js | 2k+ | WebSocket | PostgreSQL / MongoDB | 5 AI frameworks, 20+ integrations, production generator |

---

## Detailed Project Analysis

### 1. Open WebUI

**URL**: https://github.com/open-webui/open-webui
**Docs**: https://docs.openwebui.com

#### Tech Stack
| Layer | Technology |
|-------|-----------|
| Frontend | SvelteKit (Svelte 5), TypeScript, Tailwind CSS, Vite |
| Backend | Python 3.11+, FastAPI, SQLAlchemy |
| Database | SQLite (default, optional SQLCipher encryption) or PostgreSQL |
| Vector DB | Built-in support via factory pattern (Chroma, Milvus, OpenSearch, pgvector, Qdrant) |
| Real-time | Socket.IO (Redis pub/sub for multi-instance) |
| Auth | Built-in user management, OIDC/OAuth support |
| Observability | OpenTelemetry (Prometheus, Grafana, Jaeger) |

#### Architecture
- **Three-tier monorepo**: SvelteKit frontend, FastAPI backend, data layer
- **Pipeline system**: Generic Python scripts that intercept between UI and LLM — enables middleware for RAG, memory, tools, and search
- **Middleware pipeline**: Sequential handlers augment chat requests with memory, search, tools, and RAG content before forwarding to the LLM
- **Pluggable backends**: Factory pattern lets you swap databases, vector stores, storage providers, and LLM backends

#### Streaming
- Socket.IO powers real-time features (collaborative editing, presence, streaming AI responses)
- Redis pub/sub enables cross-instance synchronization for multi-worker deployments
- Supports both Ollama native protocol and OpenAI-compatible API format

#### RAG & Document Management
- Built-in document upload, chunking, embedding, and vector search
- Factory pattern supports multiple vector DB backends simultaneously
- Knowledge collections that can be shared across conversations
- Web search integration (Google, Bing, DuckDuckGo)

#### UI/UX Patterns
- Chat interface with model selector dropdown
- Conversation sidebar with search, folders, and tags
- Document/knowledge management panel
- User management and admin panel
- Mobile-responsive design
- PWA support

#### Deployment
- `pip install open-webui` for simple installs
- Docker image (single container, includes everything)
- Kubernetes Helm charts for production
- Scales horizontally with Redis + PostgreSQL

#### Pitfalls Documented
- SQLite not suitable for multi-worker production (use PostgreSQL)
- Socket.IO requires sticky sessions behind load balancers without Redis
- Large document ingestion can block the event loop without proper async handling
- Vector DB selection impacts retrieval quality significantly

---

### 2. LibreChat

**URL**: https://github.com/danny-avila/LibreChat
**Docs**: https://www.librechat.ai/docs

#### Tech Stack
| Layer | Technology |
|-------|-----------|
| Frontend | React, Tailwind CSS, shadcn/ui, Recoil, React Query |
| Backend | Express.js (Node.js) |
| Database | MongoDB (primary), MeiliSearch (full-text search) |
| Vector DB | PostgreSQL + pgvector |
| Cache | Redis |
| Auth | Multi-provider (OAuth, LDAP, email/password) |
| Build | Vite |

#### Architecture
- **Request flow**: React UI -> Recoil State -> React Query -> POST to Express API -> Auth middleware -> AI Client selection -> Stream from AI Provider -> Persist to MongoDB + index in MeiliSearch
- **AI Client abstraction**: Each provider (OpenAI, Anthropic, Google, AWS, etc.) has a dedicated client class with unified interface
- **Plugin/Action system**: OpenAPI-based actions and function calling
- **MCP integration**: Full Model Context Protocol support (stdio, websocket, sse, streamable-http transports)

#### Streaming
- SSE-based streaming from backend to frontend
- Each AI provider client handles its own streaming protocol internally
- MCP servers can use four connection types: stdio, websocket, sse, or streamable-http
- Stream chunks are buffered and persisted after completion

#### RAG & Document Management
- pgvector for vector embeddings and similarity search
- File upload with automatic processing
- Citations and source attribution in responses
- MeiliSearch for full-text conversation search across history

#### Artifacts & Advanced UI
- **Artifacts panel**: Side panel for code, documents, HTML, SVG, Mermaid diagrams
- **Code Interpreter**: Sandboxed code execution
- **Multi-model conversations**: Switch models mid-conversation
- **Message branching**: Fork conversations at any message
- **Presets**: Save and reuse model configurations

#### UI/UX Patterns
- Sidebar with conversation list, search, and folders
- Chat area with message bubbles, markdown rendering, code blocks
- Model selector with provider switching
- Artifact/preview panel (right side)
- Settings modal with extensive configuration
- Admin dashboard for user management

#### Deployment
- Docker Compose (recommended): backend + MongoDB + MeiliSearch + Redis
- Railway one-click deploy template
- Kubernetes via Helm
- Environment-based configuration (librechat.yaml)

#### Pitfalls Documented
- MongoDB schema migrations can be tricky during major version updates
- MeiliSearch indexing adds overhead — can be disabled if search not needed
- Plugin/action auth tokens must be carefully managed
- Memory usage spikes with many concurrent streams

---

### 3. LobeChat

**URL**: https://github.com/lobehub/lobe-chat
**Docs**: https://lobehub.com/docs

#### Tech Stack
| Layer | Technology |
|-------|-----------|
| Frontend | Next.js 16, React 19, Ant Design (antd), lobe-ui (AIGC components) |
| State | Zustand |
| Data Fetching | SWR + tRPC (end-to-end type-safe) |
| Backend | Next.js API routes + tRPC |
| Database | PostgreSQL + PGVector (server-side) or IndexedDB (client-side) |
| ORM | Drizzle ORM |
| Auth | Better-Auth (server DB mode), Clerk/NextAuth alternatives |
| i18n | react-i18next |
| Routing | Hybrid: Next.js App Router (static pages) + React Router DOM (SPA) |
| Testing | Vitest |

#### Architecture
- **Dual-mode deployment**: Client-side (IndexedDB, no server needed) or Server-side (PostgreSQL, multi-user)
- **CRDT synchronization**: Conflict-Free Replicated Data Type for multi-device sync
- **Plugin market**: Plugins extend function calling capabilities with custom renderers
- **Agent market**: Pre-configured agent personalities and system prompts
- **Monorepo**: @lobechat/ namespace with shared packages

#### Streaming
- Vercel AI SDK streamText for SSE-based streaming
- Provider-agnostic streaming through AI SDK abstraction
- Supports OpenAI, Anthropic, Google, AWS Bedrock, Ollama, and many more

#### RAG & Knowledge Management
- PGVector for document embeddings
- File upload (documents, images, audio, video)
- Knowledge base creation and management
- Retrieval during conversations with citations
- Multimodal support (GPT-4 Vision, etc.)

#### UI/UX Patterns
- **Sidebar**: Conversation list with search, folders, pinning
- **Chat area**: Markdown rendering, LaTeX, Mermaid, code highlighting
- **Plugin UI**: Custom rendered tool results
- **Settings**: Extensive model configuration, system prompts
- **Theme**: Light/dark mode, customizable colors
- **Mobile**: PWA with responsive design
- **TTS/STT**: Voice input and output
- **i18n**: 20+ languages

#### Deployment
- Vercel (recommended for client-side mode)
- Docker / Docker Compose (server-side mode with PostgreSQL)
- Alibaba Cloud, Zeabur, Sealos one-click deploys
- Self-hosted with custom domain

#### Pitfalls Documented
- Client-side mode (IndexedDB) has storage limitations and no cross-device sync without CRDT setup
- Plugin development documentation is sparse for advanced use cases
- Hybrid routing (Next.js App Router + React Router DOM) adds complexity
- Large knowledge bases need careful PGVector index tuning

---

### 4. Chatbot UI (by McKay Wrigley)

**URL**: https://github.com/mckaywrigley/chatbot-ui

#### Tech Stack
| Layer | Technology |
|-------|-----------|
| Frontend | Next.js, TypeScript, Tailwind CSS |
| Backend | Next.js API Routes / Supabase Edge Functions |
| Database | Supabase PostgreSQL |
| Auth | Supabase Auth (email) |
| AI | Direct OpenAI API calls |

#### Architecture
- **Supabase-centric**: All persistence and auth via Supabase
- **Migration-based schema**: `supabase/migrations/` contains the full DB setup
- Migrated from localStorage (v1) to Supabase (v2) for proper multi-device persistence
- Clean, minimal architecture — good reference for ChatGPT-like apps

#### Streaming
- OpenAI streaming API with SSE
- Token-by-token rendering in the chat UI
- Edge runtime compatible

#### Database Schema (Key Tables)
- `profiles` — user settings and preferences
- `workspaces` — organizational grouping
- `chats` — conversation metadata (title, model, etc.)
- `messages` — individual messages with role, content, timestamps
- `presets` — saved model configurations
- `prompts` — reusable prompt templates
- `files` — uploaded file metadata
- `collections` — groupings of files

#### UI/UX Patterns
- Clean ChatGPT-like interface
- Sidebar with workspace/folder hierarchy
- Chat area with streaming markdown
- Model/preset selector
- Prompt library
- File upload support

#### Deployment
- Vercel + Supabase (recommended)
- Docker for self-hosting
- Requires Supabase project setup (migrations, auth config)

#### Pitfalls Documented
- Supabase free tier limitations for heavy usage
- Migration from v1 (localStorage) to v2 (Supabase) broke existing setups
- No built-in RAG — file uploads are basic
- Limited multi-model support compared to alternatives

---

### 5. Vercel AI Chatbot (Supabase Edition)

**URL**: https://github.com/supabase-community/vercel-ai-chatbot
**Live Demo**: https://chat.vercel.ai

#### Tech Stack
| Layer | Technology |
|-------|-----------|
| Frontend | Next.js 14+ (App Router), TypeScript, Tailwind CSS, shadcn/ui |
| AI | Vercel AI SDK (streamText, generateObject) |
| Database | Supabase PostgreSQL |
| Auth | Supabase Auth (GitHub OAuth default, expandable) |
| ORM | Drizzle ORM |
| Deployment | Vercel |

#### Architecture
- **AI SDK-centric**: Uses Vercel AI SDK hooks (useChat, useCompletion) for streaming
- **AI Gateway**: Unified interface for multiple providers (OpenAI, Anthropic, Google, xAI)
- **Server Actions**: Next.js server actions for database operations
- **Middleware auth**: Next.js middleware for route protection

#### Streaming
- `streamText()` function streams AI responses in real-time via SSE
- Data stream protocol with built-in keep-alive pings and reconnection
- `consumeStream()` for server-side backpressure management
- Stream resumption on client reconnect via `resumeExistingStream`

#### Database Schema
- `users` — auth users synced from Supabase Auth
- `chats` — conversation metadata (id, title, userId, visibility, createdAt)
- `messages` — messages with role, content, chatId
- `documents` — user documents
- `suggestions` — AI-generated edit suggestions

#### UI/UX Patterns
- Minimal, polished chat interface
- Model selector dropdown (GPT-4.1 Mini default)
- Chat visibility (public/private)
- Message editing and regeneration
- Artifact/document preview
- Responsive design

#### Deployment
- One-click Vercel deploy
- Railway template available
- Supabase for database + auth (managed or self-hosted)

#### Pitfalls Documented
- AI Gateway has 5-minute timeout (not suitable for long-running agents)
- 4.5MB body size limit on Vercel serverless
- Cold starts on serverless can add latency
- Supabase free tier row limits for high-traffic apps

---

### 6. assistant-ui

**URL**: https://github.com/assistant-ui/assistant-ui
**Docs**: https://www.assistant-ui.com/docs

#### Overview
Not a full application but a **React component library** for building AI chat UIs. YC-backed. Hundreds of companies use it.

#### Tech Stack
| Layer | Technology |
|-------|-----------|
| Components | React, TypeScript, Radix UI primitives |
| Styling | Tailwind CSS, CSS variables |
| State | Internal runtime with provider pattern |
| Streaming | Provider-agnostic (AI SDK, LangGraph, Mastra, custom) |

#### Architecture
- **Composable primitives**: Like Radix UI — you compose chat UIs from small, styled primitives rather than using a monolithic component
- **Runtime abstraction**: Pluggable "runtimes" for different backends (Vercel AI SDK, LangGraph, custom REST/SSE)
- **Thread management**: Built-in conversation threading, branching, and history

#### Key Features
- Streaming with auto-scroll and accessibility
- Markdown rendering, code highlighting, LaTeX
- Tool call rendering as custom React components
- Human-in-the-loop approvals inline
- Attachments (file upload)
- Voice input (dictation)
- Keyboard shortcuts
- **Artifacts**: Sandboxed iframe rendering with real-time preview, iteration support

#### Artifact Implementation
- Artifacts render in a sandboxed iframe alongside the conversation
- Support for HTML/CSS/JS, React components, Markdown, SVG, Mermaid diagrams
- Code generation with live preview
- Iteration: user can request changes and see updates in real-time

#### Integration Points
- Works with any backend via runtime adapters
- First-class Vercel AI SDK support
- LangGraph adapter for agent workflows
- Optional Assistant Cloud for chat history and analytics

---

### 7. Vstorm Full-Stack AI Agent Template

**URL**: https://github.com/vstorm-co/full-stack-fastapi-nextjs-llm-template

#### Tech Stack
| Layer | Technology |
|-------|-----------|
| Frontend | Next.js (App Router), React, TypeScript, Tailwind CSS |
| Backend | FastAPI, Python, SQLAlchemy |
| Database | PostgreSQL or MongoDB (configurable) |
| AI Framework | PydanticAI or LangChain (selectable) |
| Cache | Redis |
| Streaming | WebSocket |
| Auth | JWT-based |
| Admin | SQLAdmin |
| Observability | Logfire, LangSmith, Sentry, Prometheus |
| Task Queue | Celery / Taskiq |

#### Architecture
- **Project generator**: Interactive CLI wizard creates fully configured projects
- **Layered backend**: Routes -> Services -> Repositories -> AI Agents
- **Agent abstraction**: Choose between PydanticAI or LangChain at project creation
- **Centralized prompts**: All agent prompts in dedicated directory
- **Dependency injection**: FastAPI DI for database sessions, auth, services

#### Streaming
- WebSocket-based real-time streaming (not SSE)
- Full event access during streaming (tool calls, intermediate steps)
- Conversation persistence to database during stream

#### Production Features
- Rate limiting (slowapi)
- Pagination (fastapi-pagination)
- Database migrations (Alembic)
- Background tasks (Celery/Taskiq)
- Webhooks
- 20+ enterprise integrations
- Docker Compose for local dev
- CI/CD pipeline templates

---

## Chat UI Best Practices

### Layout & Navigation

1. **Sidebar conversation list**: Always visible on desktop, collapsible on mobile. Show title, timestamp, and message preview. Support search, folders/tags, and pinning.

2. **Chat area**: Full-width message display with clear visual distinction between user and assistant messages. User messages right-aligned (or left for RTL), assistant messages left-aligned (or right for RTL).

3. **Input bar**: Always visible at bottom. Clear placeholder text, obvious send button, support for Shift+Enter for newlines. Show character/token count for long inputs.

4. **Model/settings**: Dropdown or header bar for model selection. Don't bury critical settings too deep.

### Message Rendering

5. **Markdown support**: Full CommonMark + GFM (tables, task lists). Code blocks with syntax highlighting and copy button. LaTeX rendering for mathematical content.

6. **Streaming display**: Token-by-token rendering with a blinking cursor or typing indicator. Smooth auto-scroll that pauses when user scrolls up. "Jump to bottom" button when scrolled away.

7. **Message actions**: Copy, edit, regenerate, delete. Show on hover to keep UI clean. Fork/branch at any message point.

### RTL & Internationalization

8. **RTL layout**: Use CSS `direction: rtl` and `text-align: right` at the root. Use logical CSS properties (`margin-inline-start` instead of `margin-left`). Mirror sidebar position. Test with actual Arabic/Hebrew content, not just English in RTL mode.

9. **Font selection**: Use fonts with strong Arabic/CJK support. IBM Plex Sans Arabic, Noto Sans Arabic, or system fonts. Ensure monospace fallbacks for code blocks.

10. **i18n**: All UI strings externalized. Date/time formatting respects locale. Number formatting for Arabic numerals if needed.

### Dark Mode & Theming

11. **Theme system**: CSS custom properties (variables) for easy theming. Respect system preference (`prefers-color-scheme`). Provide manual toggle. Test all states in both themes — errors, loading, empty states.

12. **Color contrast**: WCAG AA minimum (4.5:1 for text). Don't rely solely on color to convey meaning. Test with color blindness simulators.

### Accessibility

13. **Screen readers**: `aria-live="polite"` on streaming response containers. Proper heading hierarchy. Announce new messages.

14. **Keyboard navigation**: Tab through all interactive elements. Enter to send, Escape to cancel. Focus management when modals open/close.

15. **Focus indicators**: Visible focus rings on all interactive elements. Don't remove outline without providing alternative.

### Artifact/Preview Panel

16. **Side panel pattern**: Split view with chat on left, artifact on right (or vice versa for RTL). Resizable divider. Collapsible panel. Full-screen toggle for artifacts.

17. **Sandboxed rendering**: Use `<iframe sandbox>` for HTML/JS artifacts. Prevent artifacts from accessing parent page. CSP headers for additional security.

18. **Artifact types**: Code (with syntax highlighting), HTML (live preview), Markdown (rendered), SVG, Mermaid diagrams, React components (sandboxed).

---

## Streaming & RAG Architecture Patterns

### SSE vs WebSocket for AI Chat

| Aspect | SSE | WebSocket |
|--------|-----|-----------|
| **Direction** | Server -> Client only | Bidirectional |
| **Protocol** | HTTP (standard) | WS (upgrade handshake) |
| **Proxy compatibility** | Excellent | Problematic (firewalls, proxies) |
| **Auto-reconnect** | Built-in (EventSource API) | Manual implementation required |
| **Memory per connection** | Low (~few KB) | Higher (~70 KiB) |
| **Scaling** | Stateless, easy to load-balance | Requires sticky sessions or Redis pub/sub |
| **Best for** | Token streaming, one-way updates | Collaborative editing, real-time presence |
| **Framework support** | sse-starlette (FastAPI), native browser | FastAPI WebSocket, Socket.IO |

**Recommendation for AI chat**: SSE is the preferred choice. It handles the primary use case (streaming LLM tokens to the client) with less complexity, better proxy compatibility, and easier scaling. Use a separate POST endpoint for user messages.

**When to add WebSocket**: If you need human-in-the-loop agent approvals, collaborative features, or real-time presence indicators during streaming.

### SSE Implementation Pattern (FastAPI)

```
Client                    Server (FastAPI)
  |                           |
  |-- POST /messages -------->|  (save user message to DB)
  |                           |  (return message_id)
  |<-- 201 Created -----------|
  |                           |
  |-- GET /messages/stream -->|  (SSE endpoint)
  |                           |  (call LLM with conversation context)
  |<-- data: {"token":"Hi"} --|  (stream tokens)
  |<-- data: {"token":" th"} -|
  |<-- data: {"token":"ere"} -|
  |<-- data: {"done":true} ---|  (signal completion)
  |                           |  (save full assistant message to DB)
  |<-- connection close ------|
```

Key implementation details:
- Save user message **before** starting LLM call (crash-safe)
- Stream tokens via SSE with `text/event-stream` content type
- Include keep-alive pings every 15-30 seconds to prevent timeout
- Save complete assistant message after stream finishes
- Handle client disconnect gracefully (use `consumeStream` pattern or background task to complete saving even if client disconnects)

### Message Persistence Architecture

```
┌─────────────┐     ┌──────────────┐     ┌───────────────┐
│  UI Message  │ --> │ Core Message │ --> │ DB Message    │
│  (display)   │     │ (processing) │     │ (persistence) │
│              │     │              │     │               │
│ - id (client)│     │ - id (server)│     │ - id (PK)     │
│ - role       │     │ - role       │     │ - conv_id (FK)│
│ - content    │     │ - content    │     │ - role        │
│ - streaming  │     │ - tool_calls │     │ - content     │
│ - isError    │     │ - metadata   │     │ - metadata    │
│              │     │              │     │ - created_at  │
│              │     │              │     │ - status      │
└─────────────┘     └──────────────┘     └───────────────┘
```

**Critical**: Use **server-side generated IDs** for persisted messages. Client-side IDs are fine for optimistic UI updates but must be reconciled with server IDs after persistence.

**Message status tracking**: Store status (pending, streaming, complete, error) to handle reconnection — if client reloads during streaming, check status and resume or show partial result.

### RAG Pipeline Architecture

```
┌──────────────────────────────────────────────────────────┐
│                    INGESTION PIPELINE                     │
│                                                          │
│  Upload -> Parse -> Clean -> Chunk -> Embed -> Store     │
│   (PDF,    (text    (strip   (recursive  (voyage-3,  (pgvector, │
│    DOCX,   extract) format)  512 tokens  text-emb-3) Chroma,   │
│    TXT)                      10-20%                  Pinecone) │
│                              overlap)                          │
└──────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────┐
│                    RETRIEVAL PIPELINE                     │
│                                                          │
│  Query -> Embed -> Search -> Rerank -> Format -> Inject  │
│  (user    (same    (top-K   (cross-   (citations, (system │
│   msg)    model)   cosine)  encoder)  snippets)   prompt)│
│                                                          │
│  Hybrid search = vector similarity + keyword (BM25)      │
└──────────────────────────────────────────────────────────┘
```

#### Chunking Best Practices
- **Recursive character splitting** at 512 tokens with 10-20% overlap is the most reliable baseline (69% accuracy in benchmarks)
- **Adaptive/semantic chunking** aligned to topic boundaries can reach 87% accuracy but is more complex
- Pre-chunk documents asynchronously at upload time (not at query time)
- Store chunk metadata: source document, page number, section heading, chunk index

#### Embedding Best Practices
- Use the same embedding model for ingestion and query
- Voyage AI voyage-3-large leads benchmarks, OpenAI text-embedding-3-large is a strong alternative
- Normalize embeddings for cosine similarity
- Batch embedding calls to reduce API costs

#### Vector Search Best Practices
- HNSW indexes for sub-100ms retrieval at 95%+ recall
- Metadata filtering (by document, case, date) before vector search
- Hybrid search (vector + BM25 keyword) improves retrieval quality
- Top-K of 5-10 chunks is typically sufficient
- **Context rot**: Retrieval quality degrades as context length increases — don't stuff too many chunks

#### Citation Pattern
- Include source document name, page number, and relevant snippet
- Link citations to original documents in the UI
- Highlight cited passages in the artifact/document viewer
- Store citation metadata alongside the assistant message for persistence

### Multi-Agent Routing Patterns

```
┌─────────────────────────────────────────────────┐
│                  ROUTER AGENT                    │
│                                                  │
│  Analyze user intent -> Select specialist agent  │
│                                                  │
│  ┌─────────┐  ┌─────────┐  ┌──────────────────┐│
│  │ Legal   │  │ Document│  │ Case Analysis    ││
│  │ Q&A     │  │ Summary │  │ Agent            ││
│  │ Agent   │  │ Agent   │  │                  ││
│  └─────────┘  └─────────┘  └──────────────────┘│
│  ┌─────────┐  ┌─────────┐                      │
│  │ Citation│  │ General │                      │
│  │ Agent   │  │ Chat    │                      │
│  │         │  │ Agent   │                      │
│  └─────────┘  └─────────┘                      │
└─────────────────────────────────────────────────┘
```

**Framework comparison**:
- **LangGraph**: Graph-first (state machine with nodes, edges, conditional routing). Best for complex multi-step reasoning with clear routing and guardrails. Production-ready with LangSmith observability.
- **CrewAI**: Role-based teams with task delegation. Best for parallel task execution with specialized agents. Lower coordination overhead.
- **PydanticAI**: Type-safe agent definition with dependency injection. Best for FastAPI integration — agents are defined with Pydantic models.

**Production patterns**:
- Route by intent classification (not keyword matching)
- Fallback to general agent if confidence is low
- Log all routing decisions for debugging
- Implement guardrails (content filters, schema validation) at the router level
- Use structured output (Pydantic models) for inter-agent communication

---

## Typical Errors & Debugging

### SSE Streaming Issues

| Error | Cause | Fix |
|-------|-------|-----|
| **Stream cuts off mid-response** | Proxy/CDN timeout (usually 30-60s) | Add SSE keep-alive pings every 15s; configure proxy timeouts |
| **Duplicate messages on reconnect** | Client EventSource auto-reconnects and replays | Use `Last-Event-ID` header; deduplicate by message ID on client |
| **Partial message not saved** | Client disconnects before stream completes | Use `consumeStream()` pattern — run LLM call to completion in background task even after client disconnect |
| **CORS errors on SSE** | Browser blocks cross-origin EventSource | Set `Access-Control-Allow-Origin` and `Access-Control-Allow-Credentials` headers |
| **SSE blocked by buffering proxy** | Nginx/CloudFlare buffers SSE responses | Set `X-Accel-Buffering: no` header; disable proxy buffering for SSE endpoints |
| **Memory leak from unclosed streams** | Server doesn't clean up on client disconnect | Use FastAPI `Request.is_disconnected()` check in generator; implement proper cleanup |

### Message Persistence Issues

| Error | Cause | Fix |
|-------|-------|-----|
| **Message saved twice** | Optimistic UI + server save race condition | Use idempotency keys; check for existing message before insert |
| **Tool results missing from saved messages** | Only text content saved, tool_calls dropped | Save full message object including tool_calls and tool_results as JSONB |
| **Conversation order wrong after edit** | Messages reordered by created_at but edited messages keep old timestamp | Use explicit `position` or `sequence` column, not just timestamp |
| **Chat history too large for context** | All messages sent to LLM | Implement sliding window (last N messages) or summarization of older messages |
| **ID mismatch between client and server** | Client generates UUID, server generates different one | Return server ID in response; reconcile client-side after save |

### RAG Pipeline Issues

| Error | Cause | Fix |
|-------|-------|-----|
| **Irrelevant chunks retrieved** | Poor chunking (too small or too large) | Use recursive splitting at 512 tokens with 10-20% overlap; test with actual queries |
| **"I don't have information about that"** | Embedding model mismatch between ingestion and query | Always use the same embedding model for both |
| **Slow vector search** | Missing HNSW index or table scan | Create HNSW index on vector column; add metadata filters to reduce search space |
| **Hallucinated citations** | LLM generates fake sources | Include actual chunk text in prompt; validate citations against retrieved chunks |
| **PDF parsing failures** | Complex layouts, scanned documents, Arabic text | Use specialized parsers (pdfplumber, PyMuPDF); add OCR fallback; test with actual documents |
| **Embedding dimension mismatch** | Changed embedding model without re-embedding | Re-embed all documents when changing models; store model version in metadata |
| **Context rot** | Too many chunks stuffed into prompt | Limit to 5-10 most relevant chunks; use reranking to select best matches |

### Authentication & Session Issues

| Error | Cause | Fix |
|-------|-------|-----|
| **JWT expired during long stream** | Token expires mid-conversation | Refresh token before starting stream; implement token refresh in SSE reconnect |
| **Supabase RLS blocks query** | Missing or incorrect policy | Verify RLS policies with `auth.uid()` matching user_id column |
| **Stateless JWT can't be revoked** | No server-side session store | Accept as limitation or add token blocklist in Redis |
| **CORS preflight fails** | OPTIONS not handled for SSE endpoint | Explicitly handle OPTIONS requests; add CORS middleware before SSE routes |

### Deployment Issues

| Error | Cause | Fix |
|-------|-------|-----|
| **Cold start latency** | Serverless function initialization | Use Railway (always warm) or Vercel edge runtime; preload models |
| **WebSocket upgrade fails** | Load balancer/proxy doesn't support upgrade | Use SSE instead; or configure proxy for WebSocket upgrade |
| **Docker image too large** | All dev dependencies included | Multi-stage build; separate build and runtime stages |
| **Environment variables missing** | Different between local and production | Use `.env.example` as checklist; validate all required vars at startup |
| **Database connection exhaustion** | Too many connections from serverless functions | Use connection pooling (PgBouncer, Supabase connection pooler) |
| **Memory OOM on large file upload** | File loaded entirely into memory | Stream file uploads; process in chunks; set upload size limits |

### Frontend-Specific Issues

| Error | Cause | Fix |
|-------|-------|-----|
| **Auto-scroll fights user scroll** | Scroll-to-bottom triggers while user reads earlier messages | Detect user scroll direction; pause auto-scroll when user scrolls up; show "jump to bottom" button |
| **Markdown XSS** | Unsanitized HTML in markdown rendering | Use DOMPurify or rehype-sanitize; never use `dangerouslySetInnerHTML` with user content |
| **RTL layout breaks with code blocks** | Code is always LTR but container is RTL | Use `dir="auto"` on code blocks; set `direction: ltr` on `<pre>` and `<code>` elements |
| **State desync after tab switch** | React Query cache stale after backgrounding | Configure `refetchOnWindowFocus`; use `staleTime` appropriately |
| **Hydration mismatch** | Server renders different state than client | Ensure auth state is consistent; use `suppressHydrationWarning` for theme |

---

## Cross-Project Architecture Comparison

| Feature | Open WebUI | LibreChat | LobeChat | Chatbot UI | Vercel AI Chatbot |
|---------|-----------|-----------|----------|-----------|------------------|
| **Frontend** | SvelteKit | React | Next.js | Next.js | Next.js |
| **Backend** | FastAPI (Python) | Express (Node.js) | Next.js API/tRPC | Supabase | Vercel Serverless |
| **Database** | SQLite/PostgreSQL | MongoDB | PostgreSQL | Supabase PG | Supabase PG |
| **Streaming** | Socket.IO | SSE | SSE (AI SDK) | SSE | SSE (AI SDK) |
| **Vector DB** | Multi (factory) | pgvector | PGVector | None | None |
| **Auth** | Built-in + OIDC | Multi-provider | Better-Auth | Supabase Auth | Supabase Auth |
| **Artifacts** | No | Yes | No | No | Partial |
| **Agents/MCP** | Pipelines | MCP + Agents | Plugins | No | No |
| **Mobile** | Responsive | Responsive | PWA | Responsive | Responsive |
| **i18n** | Yes | Partial | Yes (20+) | No | No |
| **Self-host** | Docker/pip | Docker Compose | Docker/Vercel | Vercel + Supabase | Vercel + Supabase |

---

## Key Takeaways for Luna Legal AI

1. **SSE is the right choice** for streaming LLM responses. All major projects use it (or are migrating to it). FastAPI + sse-starlette is a well-proven combination.

2. **Supabase + PostgreSQL + pgvector** is a strong stack for auth, persistence, and RAG — used by Chatbot UI, Vercel AI Chatbot, and LobeChat (server mode).

3. **Save user messages before LLM call** — every successful project follows this crash-safe pattern.

4. **Artifact panel** is a differentiator: LibreChat and assistant-ui demonstrate the side-panel pattern with sandboxed iframe rendering. For Luna, legal document previews and citation highlighting would be the key use cases.

5. **Agent routing** should use intent classification, not keyword matching. LangGraph or PydanticAI are the best fits for a FastAPI backend.

6. **RAG chunking at 512 tokens with 10-20% overlap** is the safe baseline. Hybrid search (vector + keyword) improves quality for legal documents.

7. **Multi-stage Docker builds** are standard for deployment. Railway is preferred over Vercel for Python backends (always warm, no cold starts, persistent processes).

8. **RTL support** is rare in existing projects — Luna will need custom work here. Key: use CSS logical properties, `dir="auto"` on code blocks, and test with real Arabic content.

9. **Common failure modes** to guard against: SSE proxy buffering, client disconnect during streaming, JWT expiry during long conversations, and context rot from too many RAG chunks.

---

## Sources

### Project Repositories
- [Open WebUI — GitHub](https://github.com/open-webui/open-webui)
- [Open WebUI — Features Documentation](https://docs.openwebui.com/features/)
- [Open WebUI — Architecture (DeepWiki)](https://deepwiki.com/open-webui/open-webui/2-architecture)
- [LibreChat — GitHub](https://github.com/danny-avila/LibreChat)
- [LibreChat — Architecture Documentation (Gist)](https://gist.github.com/ChakshuGautam/fca45e48a362b6057b5e67145b82a994)
- [LibreChat — Official Site](https://www.librechat.ai/)
- [LobeChat — GitHub](https://github.com/lobehub/lobe-chat)
- [LobeChat — Architecture Wiki](https://github.com/lobehub/lobe-chat/wiki/Architecture)
- [LobeChat — DeepWiki](https://deepwiki.com/lobehub/lobe-chat)
- [Chatbot UI — GitHub](https://github.com/mckaywrigley/chatbot-ui)
- [Vercel AI Chatbot (Supabase) — GitHub](https://github.com/supabase-community/vercel-ai-chatbot)
- [assistant-ui — GitHub](https://github.com/assistant-ui/assistant-ui)
- [assistant-ui — Documentation](https://www.assistant-ui.com/docs)
- [Vstorm Full-Stack Template — GitHub](https://github.com/vstorm-co/full-stack-fastapi-nextjs-llm-template)

### Architecture & Streaming
- [SSE vs WebSocket for AI Chat — sniki.dev](https://www.sniki.dev/posts/sse-vs-websockets-for-ai-chat/)
- [Streaming in 2026: SSE vs WebSockets vs RSC — JetBI](https://jetbi.com/blog/streaming-architecture-2026-beyond-websockets)
- [Comparing Real-Time Communication Options — Medium](https://tech-depth-and-breadth.medium.com/comparing-real-time-communication-options-http-streaming-sse-or-websockets-for-conversational-74c12f0bd7bc)
- [AI SDK UI: Stream Protocols — Vercel](https://ai-sdk.dev/docs/ai-sdk-ui/stream-protocol)
- [AI SDK UI: Chatbot Message Persistence — Vercel](https://ai-sdk.dev/docs/ai-sdk-ui/chatbot-message-persistence)
- [AI SDK UI: Chatbot Resume Streams — Vercel](https://ai-sdk.dev/docs/ai-sdk-ui/chatbot-resume-streams)
- [Streaming AI Agent Responses with SSE — Medium](https://akanuragkumar.medium.com/streaming-ai-agents-responses-with-server-sent-events-sse-a-technical-case-study-f3ac855d0755)

### RAG & Vector Search
- [RAG Pipeline Deep Dive — DEV Community](https://dev.to/derrickryangiggs/rag-pipeline-deep-dive-ingestion-chunking-embedding-and-vector-search-2877)
- [Best Chunking Strategies for RAG — Firecrawl](https://www.firecrawl.dev/blog/best-chunking-strategies-rag)
- [Chunking Strategies for RAG — Weaviate](https://weaviate.io/blog/chunking-strategies-for-rag)
- [Building Production-Ready RAG in FastAPI — DEV Community](https://dev.to/hamluk/building-production-ready-rag-in-fastapi-with-vector-databases-39gf)
- [Full Stack AI in 2025: RAG with Next.js, FastAPI & Llama 3](https://metadesignsolutions.com/full-stack-ai-building-rag-apps-with-next-js-fastapi-and-llama-3-retrievalaugmented-generation-vector-dbs/)
- [RAG Infrastructure: Production Guide — Introl](https://introl.com/blog/rag-infrastructure-production-retrieval-augmented-generation-guide)

### Agent Frameworks
- [Best AI Agent Frameworks 2025 — Maxim](https://www.getmaxim.ai/articles/top-5-ai-agent-frameworks-in-2025-a-practical-guide-for-ai-builders/)
- [Comparing AI Agent Frameworks — IBM Developer](https://developer.ibm.com/articles/awb-comparing-ai-agent-frameworks-crewai-langgraph-and-beeai/)
- [Agent Orchestration 2026 — Iterathon](https://iterathon.tech/blog/ai-agent-orchestration-frameworks-2026)
- [LangGraph & Next.js Integration — Akveo](https://www.akveo.com/blog/langgraph-and-nextjs-how-to-integrate-ai-agents-in-a-modern-web-stack)
- [From LangChain Demos to Production FastAPI — DEV Community](https://dev.to/hamluk/from-langchain-demos-to-a-production-ready-fastapi-backend-1c0a)

### UI/UX Best Practices
- [AI Chat UI Best Practices — DEV Community](https://dev.to/greedy_reader/ai-chat-ui-best-practices-designing-better-llm-interfaces-18jj)
- [16 Chat UI Design Patterns — BricxLabs](https://bricxlabs.com/blogs/message-screen-ui-deisgn)
- [Chat App Design Best Practices — CometChat](https://www.cometchat.com/blog/chat-app-design-best-practices)
- [Implementing Claude's Artifacts Feature — LogRocket](https://blog.logrocket.com/implementing-claudes-artifacts-feature-ui-visualization/)
- [Open Artifacts — GitHub](https://github.com/13point5/open-artifacts)
- [Open Source AI Artifacts — Vercel Template](https://vercel.com/templates/next.js/open-source-ai-artifacts)

### Deployment
- [Vercel vs Railway vs Render: AI Deployment — Athenic](https://getathenic.com/blog/vercel-vs-railway-vs-render-ai-deployment)
- [AI Agent Deployment Platforms — Athenic](https://getathenic.com/blog/ai-agent-deployment-platforms-vercel-aws-railway)
- [LibreChat vs Open WebUI Comparison — Portkey](https://portkey.ai/blog/librechat-vs-openwebui/)
- [Top 5 Open-Source ChatGPT Replacements — APIpie](https://apipie.ai/docs/blog/top-5-opensource-chatgpt-replacements)
- [5 Best Open Source Chat UIs for LLMs — Medium](https://poornaprakashsr.medium.com/5-best-open-source-chat-uis-for-llms-in-2025-11282403b18f)
