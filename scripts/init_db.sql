-- Enable extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS vector;          -- pgvector
SELECT create_hypertable('klines', 'ts', if_not_exists => TRUE) WHERE EXISTS (
    SELECT FROM information_schema.tables WHERE table_name = 'klines'
);
