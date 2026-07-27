-- Colim ledger. One row per package; every number in any report, dashboard or
-- README must be a query over these tables. No number lives anywhere else.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- Run-level provenance: eval toolchain, index snapshot, run ids.
CREATE TABLE IF NOT EXISTS meta (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS packages (
    pkg_key             TEXT PRIMARY KEY,   -- reservoir "<owner>/<name>", stable id
    owner               TEXT NOT NULL,
    name                TEXT NOT NULL,
    repo                TEXT,               -- github "<owner>/<repo>", may differ from pkg_key
    repo_url            TEXT,
    default_branch      TEXT,
    stars               INTEGER,
    license             TEXT,

    -- Census scope. archived => excluded from K, counted separately.
    archived            INTEGER,            -- 1/0/NULL(unknown)
    source_available    INTEGER NOT NULL DEFAULT 1,  -- 0 = repo deleted/private since indexing
    is_fork             INTEGER,
    last_commit         TEXT,               -- never an exclusion criterion; enables the
                                            -- "active in last 12 months" slice
    in_k                INTEGER NOT NULL,   -- census denominator membership
    release_train       INTEGER NOT NULL DEFAULT 0,  -- in K, excluded from campaign
    mathlib_downstream  INTEGER,            -- 1/0/NULL(unknown) -> K_m denominator

    -- Toolchains.
    old_toolchain       TEXT,               -- repo's own pin at latest indexed revision
    target_toolchain    TEXT,               -- the evaluation toolchain
    eval_revision       TEXT,               -- revision Reservoir built on target_toolchain
    eval_build_url      TEXT,               -- public Actions log for the red/green state
    eval_run_at         TEXT,
    last_green_toolchain TEXT,              -- newest toolchain with a recorded green
    last_green_run_at   TEXT,

    -- Failure characterisation (populated Day 2 from real logs).
    red_state_log_hash  TEXT,
    error_classes       TEXT,               -- json array
    failure_origin      TEXT,               -- self | dependency | both | unknown
    first_error         TEXT,               -- first attributable error line, verbatim
    error_file_count    INTEGER,            -- distinct .lean files with errors
    error_files         TEXT,               -- json array, capped
    infra_only          INTEGER,            -- 1 = failed only on runner infrastructure
    -- WHY we believe this package is red. Governs how the row may be quoted.
    --   code_error            log shows Lean/Lake errors in real code
    --   infra_release         pinned release artifact no longer downloads;
    --                         genuine persistent dependency-acquisition failure
    --   infra_uninformative   only a cache-fetch failure; Reservoir could not
    --                         tell us anything -- awaiting measurement
    --   measured_local        we rebuilt it here and saw the outcome ourselves
    --   measured_actions      rebuilt via our own Actions matrix, log public
    red_basis           TEXT,

    -- Repair pipeline.
    tier1_result        TEXT,
    tier2_attempt_count INTEGER NOT NULL DEFAULT 0,
    statement_diff_verdict TEXT,            -- pass | flagged | fail | NULL
    branch_url          TEXT,
    ci_run_url          TEXT,
    dep_fork_override   INTEGER NOT NULL DEFAULT 0,
    dep_fork_note       TEXT,

    -- Census classes:   GREEN_CURRENT | RED_REGRESSION | NEVER_GREEN | UNKNOWN
    --                 | FORCED_DOWNGRADE  (pins newer than eval tc; forced backwards)
    -- Scope exclusions: ARCHIVED | VANISHED   (both in_k=0, never in M)
    -- Repair outcomes:  REPAIRED | FLAGGED_STATEMENT_CHANGE | CANNOT_FIX
    final_status        TEXT NOT NULL,
    status_reason       TEXT,               -- why UNKNOWN, why excluded: audit trail

    updated_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_packages_status ON packages(final_status);
CREATE INDEX IF NOT EXISTS idx_packages_k ON packages(in_k);
CREATE INDEX IF NOT EXISTS idx_packages_mathlib ON packages(mathlib_downstream);

-- Every Reservoir build observation we relied on, so a skeptic can re-derive
-- the classification without re-cloning the index.
CREATE TABLE IF NOT EXISTS build_observations (
    pkg_key     TEXT NOT NULL REFERENCES packages(pkg_key),
    toolchain   TEXT NOT NULL,
    revision    TEXT,
    built       INTEGER NOT NULL,
    run_at      TEXT NOT NULL,
    url         TEXT,
    PRIMARY KEY (pkg_key, toolchain, run_at)
);

CREATE INDEX IF NOT EXISTS idx_obs_toolchain ON build_observations(toolchain);

-- Local reproductions of Reservoir's recorded state. The census trusts
-- Reservoir; this table is how we check Reservoir against reality.
CREATE TABLE IF NOT EXISTS reproductions (
    pkg_key         TEXT NOT NULL REFERENCES packages(pkg_key),
    revision        TEXT,
    toolchain       TEXT NOT NULL,
    reservoir_built INTEGER NOT NULL,   -- what Reservoir recorded
    local_built     INTEGER NOT NULL,   -- what we got
    agrees          INTEGER NOT NULL,   -- reservoir_built == local_built
    failed_step     TEXT,               -- clone | cache | build
    exit_code       INTEGER,
    timed_out       INTEGER NOT NULL DEFAULT 0,
    duration_s      REAL,
    log_path        TEXT,
    log_sha256      TEXT,
    error_classes   TEXT,               -- json array, from the local log
    failure_origin  TEXT,
    created_at      TEXT NOT NULL,
    PRIMARY KEY (pkg_key, toolchain)
);

-- Tier-2 attempt log: a first-class deliverable (training-data flywheel).
-- Every attempt is logged, success or failure.
CREATE TABLE IF NOT EXISTS attempts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    pkg_key         TEXT NOT NULL REFERENCES packages(pkg_key),
    file            TEXT NOT NULL,
    iteration       INTEGER NOT NULL,
    model           TEXT NOT NULL,
    model_params    TEXT,                   -- json
    prompt_hash     TEXT NOT NULL,
    error_before    TEXT,
    patch           TEXT,
    verdict         TEXT NOT NULL,          -- fixed | still_red | cannot_fix | budget_exhausted
    cost_usd        REAL NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_attempts_pkg ON attempts(pkg_key);
