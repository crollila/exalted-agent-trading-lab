CREATE TABLE IF NOT EXISTS strategies (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    strategy_id TEXT NOT NULL,
    starting_equity REAL NOT NULL,
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ended_at TEXT,
    status TEXT NOT NULL,
    FOREIGN KEY(strategy_id) REFERENCES strategies(id)
);

CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id TEXT,
    equity REAL NOT NULL,
    cash REAL NOT NULL,
    timestamp TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id TEXT,
    symbol TEXT NOT NULL,
    quantity REAL NOT NULL,
    market_value REAL NOT NULL,
    average_entry_price REAL,
    timestamp TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trade_proposals (
    proposal_id TEXT PRIMARY KEY,
    strategy_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    action TEXT NOT NULL,
    asset_class TEXT NOT NULL,
    target_weight REAL,
    quantity REAL,
    estimated_price REAL NOT NULL,
    thesis TEXT NOT NULL,
    confidence REAL NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS risk_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    proposal_id TEXT NOT NULL,
    approved INTEGER NOT NULL,
    reasons_json TEXT NOT NULL,
    approved_quantity REAL,
    estimated_trade_value REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    FOREIGN KEY(proposal_id) REFERENCES trade_proposals(proposal_id)
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    proposal_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    action TEXT NOT NULL,
    quantity REAL NOT NULL,
    order_type TEXT NOT NULL,
    limit_price REAL,
    dry_run INTEGER NOT NULL,
    submitted INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(proposal_id) REFERENCES trade_proposals(proposal_id)
);

CREATE TABLE IF NOT EXISTS benchmark_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    benchmark_symbol TEXT NOT NULL,
    starting_equity REAL NOT NULL,
    current_strategy_equity REAL NOT NULL,
    starting_benchmark_price REAL NOT NULL,
    current_benchmark_price REAL NOT NULL,
    timestamp TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id TEXT NOT NULL,
    report_date TEXT NOT NULL,
    report_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
