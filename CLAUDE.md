# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Architecture Overview

This is a **multi-application crypto trading platform** with a **Django backend** and **Next.js frontend**, focused on cryptocurrency trading strategies, analysis, and automated trading assistance.

### Technology Stack
- **Backend**: Django 4.x with Django REST Framework, WebSockets (Channels), Redis, InfluxDB
- **Frontend**: Next.js 15.x (React 18.x), Mantine UI 8.x, Redux Toolkit, Lightweight Charts 5.x
- **Trading Integration**: CCXT for exchange APIs (Binance, OKX), custom WebSocket feeds
- **AI/ML**: OpenAI, CrewAI agents, scikit-learn, pandas-ta for technical analysis
- **Database**: SQLite (dev), Redis (caching/WebSockets), InfluxDB (time-series)
- **Package Manager**: uv for Python dependencies

### Multi-App Architecture

#### Backend (`/backend/`)
Django apps under `backend/`:
- `authentication/` - User auth with JWT, captcha, password reset
- `assistant/` - AI-powered trading assistance and chat
- `trade/` - Trading records, portfolio management
- `qtbot/` - Quantitative trading bot logic
- `qtcore/` - Core trading engine (Binance/OKX WebSocket, scheduler)
- `strategy/` - Strategy definitions and backtesting
- `ai_agent/` - CrewAI-based autonomous trading agents
- `notify/` - Notification system (WebSocket consumers)

#### Frontend (`/frontend/`)
- **Main trading interface** (Next.js 15.x): Real-time charts, AI chat, portfolio tracking
- **Electron desktop app**: `electron/main.js` - Cross-platform desktop support (Mac/Windows/Linux)
- **Cross-platform**: Android (Capacitor), Tauri desktop support

## Development Commands

### Backend
```bash
cd backend

# Environment setup
uv venv && source venv/bin/activate
uv pip install -r requirements.txt

# Database
uv run python manage.py makemigrations
uv run python manage.py migrate
uv run python manage.py createsuperuser

# Development server (REST API)
uv run python manage.py runserver  # http://localhost:8000

# WebSocket server
uv run daphne -b 0.0.0.0 -p 8000 core.asgi:application

# Run tests
uv run python manage.py test
uv run python manage.py test assistant.tests.test_views
```

### Frontend
```bash
cd frontend

# Install dependencies
npm install

# Development server
npm run dev  # http://localhost:3000

# Build for production
npm run build

# Lint
npm run lint

# Electron development
npm run electron-dev  # Runs Next.js + Electron concurrently
```

### Infrastructure
```bash
# Redis (required for WebSockets) - default port 6379
redis-server

# InfluxDB (time-series data) - default port 8086
influxd

# Reset database
rm backend/db.sqlite3 && cd backend && uv run python manage.py migrate
```

## Key Integration Points

### API Routes
All APIs under `/api/`:
- `/api/strategy/` - Strategy management
- `/api/notify/` - Notifications
- `/api/trade/` - Trading records
- `/api/qtbot/` - Bot configuration
- `/api/assistant/` - AI assistant
- `/api/ai_agent/` - CrewAI agents

### WebSocket Endpoints
- `/ws/market-data/` - Market data streams
- `/ws/trading/` - Trading operations
- `/ws/assistant/` - AI chat
- `/ws/notify/` - Notifications

### Environment Variables (`backend/.env`)
```bash
# AI
OPENAI_API_KEY

# Exchanges (code-configured)
BINANCE_API_KEY, BINANCE_SECRET
OKX_API_KEY, OKX_SECRET

# Database
INFLUXDB_URL, INFLUXDB_TOKEN
REDIS_URL

# Frontend
FRONTEND_URL
```

## Code Patterns

### Frontend
- **State**: Redux Toolkit (`/store/modules/`)
- **API**: Axios services (`/services/`)
- **Charts**: Lightweight Charts for trading views
- **Editor**: BlockNote for trading plans
- **Components**: Organized by feature under `/Components/`

### Backend
- **WebSocket Consumers**: `qtcore/consumers/`, `notify/consumers/`
- **Exchange Integration**: `qtcore/binance_*.py`, `qtcore/okx_*.py`
- **Serializers**: DRF serializers for API responses
- **Tasks**: django-apscheduler in `qtcore/scheduler.py`

## Deployment
- Frontend Docker: `frontend/Dockerfile` (multi-stage Alpine build)
- Desktop builds: `npm run dist` (electron-builder)
- Environment-specific settings in `backend/core/settings.py`
