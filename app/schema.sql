-- =============================================================================
-- Airutil Relational Database Schema
--
-- OS Concepts Demonstrated:
-- 1. Relational Persistence & ACID Guarantees: Multi-client transactions across
--    replicated application workers via PostgreSQL IPC socket connections.
-- 2. Foreign Key Constraints & Cascading: Kernel-enforced relational integrity.
-- 3. B-Tree & GIN Indexes: Inverted indexes (GIN) for high-speed full-text search
--    over document contents and metadata.
-- =============================================================================

CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(50) UNIQUE NOT NULL,
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS courses (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    course_code VARCHAR(32) NOT NULL,
    course_name VARCHAR(128) NOT NULL,
    semester VARCHAR(32) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT unique_user_course UNIQUE(user_id, course_code)
);

CREATE TABLE IF NOT EXISTS files (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    filename VARCHAR(255) NOT NULL,
    original_name VARCHAR(255) NOT NULL DEFAULT '',
    storage_name VARCHAR(255) NOT NULL DEFAULT '',
    file_path VARCHAR(512) NOT NULL,
    file_size BIGINT NOT NULL,
    mime_type VARCHAR(128) DEFAULT 'application/octet-stream',
    category VARCHAR(64) DEFAULT 'Others',
    course VARCHAR(64) DEFAULT 'General',
    sha256 VARCHAR(64) DEFAULT '',
    tags TEXT[] DEFAULT '{}',
    is_deleted BOOLEAN DEFAULT FALSE,
    deleted_at TIMESTAMPTZ DEFAULT NULL,
    extracted_text TEXT DEFAULT '',
    thumbnail_path VARCHAR(512) DEFAULT '',
    search_vector tsvector,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- Migration helpers for schema updates
ALTER TABLE files ADD COLUMN IF NOT EXISTS original_name VARCHAR(255) NOT NULL DEFAULT '';
ALTER TABLE files ADD COLUMN IF NOT EXISTS storage_name VARCHAR(255) NOT NULL DEFAULT '';
ALTER TABLE files ADD COLUMN IF NOT EXISTS course VARCHAR(64) DEFAULT 'General';
ALTER TABLE files ADD COLUMN IF NOT EXISTS sha256 VARCHAR(64) DEFAULT '';
ALTER TABLE files ADD COLUMN IF NOT EXISTS is_deleted BOOLEAN DEFAULT FALSE;
ALTER TABLE files ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ DEFAULT NULL;
ALTER TABLE files ADD COLUMN IF NOT EXISTS extracted_text TEXT DEFAULT '';
ALTER TABLE files ADD COLUMN IF NOT EXISTS thumbnail_path VARCHAR(512) DEFAULT '';
ALTER TABLE files ADD COLUMN IF NOT EXISTS search_vector tsvector;

-- Set default original_name from filename if empty
UPDATE files SET original_name = filename WHERE original_name = '' OR original_name IS NULL;
UPDATE files SET storage_name = filename WHERE storage_name = '' OR storage_name IS NULL;

CREATE TABLE IF NOT EXISTS vault_items (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title VARCHAR(128) NOT NULL,
    username VARCHAR(128),
    encrypted_password TEXT NOT NULL,
    url VARCHAR(512),
    notes TEXT,
    category VARCHAR(64) DEFAULT 'general',
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS share_links (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    share_token VARCHAR(64) UNIQUE NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    max_downloads INTEGER DEFAULT NULL,
    download_count INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS activity_log (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    action VARCHAR(64) NOT NULL,
    details TEXT,
    ip_address VARCHAR(64),
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- Full-Text Search Vector Generation Function and Trigger
CREATE OR REPLACE FUNCTION update_files_search_vector() RETURNS trigger AS $$
BEGIN
    NEW.search_vector :=
        setweight(to_tsvector('english', coalesce(NEW.original_name, '')), 'A') ||
        setweight(to_tsvector('english', coalesce(NEW.course, '')), 'B') ||
        setweight(to_tsvector('english', array_to_string(coalesce(NEW.tags, '{}'), ' ')), 'B') ||
        setweight(to_tsvector('english', coalesce(NEW.extracted_text, '')), 'C');
    RETURN NEW;
END
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_update_files_search_vector ON files;
CREATE TRIGGER trg_update_files_search_vector
BEFORE INSERT OR UPDATE ON files
FOR EACH ROW EXECUTE FUNCTION update_files_search_vector();

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_users_username ON users(LOWER(username));
CREATE INDEX IF NOT EXISTS idx_users_email ON users(LOWER(email));
CREATE INDEX IF NOT EXISTS idx_courses_user_id ON courses(user_id);
CREATE INDEX IF NOT EXISTS idx_files_user_id ON files(user_id);
CREATE INDEX IF NOT EXISTS idx_files_course ON files(course);
CREATE INDEX IF NOT EXISTS idx_files_sha256 ON files(sha256);
CREATE INDEX IF NOT EXISTS idx_files_is_deleted ON files(is_deleted);
CREATE INDEX IF NOT EXISTS idx_files_search_vector ON files USING gin(search_vector);
CREATE INDEX IF NOT EXISTS idx_vault_items_user_id ON vault_items(user_id);
CREATE INDEX IF NOT EXISTS idx_share_links_token ON share_links(share_token);
CREATE INDEX IF NOT EXISTS idx_activity_log_user_id ON activity_log(user_id);
