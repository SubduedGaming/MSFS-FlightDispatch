"""SQL migrations. Append new entries; never edit an existing one."""

MIGRATIONS: list[str] = [
    # 1 --------------------------------------------------------------
    """
    CREATE TABLE pilot (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        name TEXT NOT NULL,
        callsign TEXT NOT NULL,
        home_icao TEXT NOT NULL,
        balance REAL NOT NULL DEFAULT 0,
        reputation REAL NOT NULL DEFAULT 50,
        xp INTEGER NOT NULL DEFAULT 0,
        total_minutes REAL NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    );
    CREATE TABLE airports (
        icao TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        city TEXT NOT NULL DEFAULT '',
        country TEXT NOT NULL DEFAULT '',
        lat REAL NOT NULL,
        lon REAL NOT NULL,
        elevation_ft REAL NOT NULL DEFAULT 0,
        runway_ft INTEGER NOT NULL DEFAULT 0,
        size TEXT NOT NULL DEFAULT 'S'
    );
    CREATE TABLE hangar (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        type_id TEXT NOT NULL,
        registration TEXT NOT NULL UNIQUE,
        nickname TEXT NOT NULL DEFAULT '',
        location_icao TEXT NOT NULL,
        hours_total REAL NOT NULL DEFAULT 0,
        hours_since_inspection REAL NOT NULL DEFAULT 0,
        condition REAL NOT NULL DEFAULT 100,
        fuel_gal REAL NOT NULL DEFAULT 0,
        purchase_price REAL NOT NULL DEFAULT 0,
        purchased_at TEXT NOT NULL,
        sold INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kind TEXT NOT NULL,
        title TEXT NOT NULL,
        origin TEXT NOT NULL,
        dest TEXT NOT NULL,
        distance_nm REAL NOT NULL,
        pax INTEGER NOT NULL DEFAULT 0,
        cargo_lb INTEGER NOT NULL DEFAULT 0,
        client TEXT NOT NULL DEFAULT '',
        briefing TEXT NOT NULL DEFAULT '',
        payout REAL NOT NULL,
        min_category TEXT NOT NULL DEFAULT 'piston',
        min_runway_ft INTEGER NOT NULL DEFAULT 0,
        min_reputation REAL NOT NULL DEFAULT 0,
        deadline_minutes INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'offered',
        aircraft_id INTEGER REFERENCES hangar(id),
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        accepted_at TEXT
    );
    CREATE INDEX idx_jobs_status ON jobs(status);
    CREATE TABLE flights (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id INTEGER REFERENCES jobs(id),
        aircraft_id INTEGER REFERENCES hangar(id),
        sim_title TEXT NOT NULL DEFAULT '',
        dep TEXT NOT NULL DEFAULT '',
        arr TEXT NOT NULL DEFAULT '',
        started_at TEXT NOT NULL,
        ended_at TEXT,
        block_min REAL NOT NULL DEFAULT 0,
        air_min REAL NOT NULL DEFAULT 0,
        distance_nm REAL NOT NULL DEFAULT 0,
        fuel_used_gal REAL NOT NULL DEFAULT 0,
        landing_fpm REAL,
        max_g REAL NOT NULL DEFAULT 1,
        max_ias REAL NOT NULL DEFAULT 0,
        max_alt_ft REAL NOT NULL DEFAULT 0,
        overspeed_s REAL NOT NULL DEFAULT 0,
        score REAL NOT NULL DEFAULT 0,
        outcome TEXT NOT NULL DEFAULT 'in_progress',
        payout REAL NOT NULL DEFAULT 0,
        costs REAL NOT NULL DEFAULT 0,
        summary TEXT NOT NULL DEFAULT '',
        debrief TEXT NOT NULL DEFAULT ''
    );
    CREATE TABLE telemetry (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        flight_id INTEGER NOT NULL REFERENCES flights(id) ON DELETE CASCADE,
        t REAL NOT NULL,
        lat REAL, lon REAL, alt_ft REAL, ias REAL, gs REAL, hdg REAL, vs REAL,
        fuel_gal REAL, on_ground INTEGER
    );
    CREATE INDEX idx_tel_flight ON telemetry(flight_id);
    CREATE TABLE flight_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        flight_id INTEGER NOT NULL REFERENCES flights(id) ON DELETE CASCADE,
        t REAL NOT NULL,
        kind TEXT NOT NULL,
        detail TEXT NOT NULL DEFAULT ''
    );
    CREATE TABLE transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        amount REAL NOT NULL,
        category TEXT NOT NULL,
        description TEXT NOT NULL,
        balance_after REAL NOT NULL
    );
    CREATE TABLE aircraft_flown (
        sim_title TEXT PRIMARY KEY,
        type_id TEXT,
        first_flown TEXT NOT NULL,
        last_flown TEXT NOT NULL,
        flights INTEGER NOT NULL DEFAULT 0,
        minutes REAL NOT NULL DEFAULT 0
    );
    CREATE TABLE messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        role TEXT NOT NULL,
        content TEXT NOT NULL
    );
    CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    """,
    # 2 -- employers, qualifications, messenger threads ---------------------
    """
    ALTER TABLE pilot ADD COLUMN skill REAL NOT NULL DEFAULT 40;
    ALTER TABLE pilot ADD COLUMN location_icao TEXT NOT NULL DEFAULT '';
    ALTER TABLE flights ADD COLUMN type_id TEXT NOT NULL DEFAULT '';
    ALTER TABLE flights ADD COLUMN employer_id TEXT;
    ALTER TABLE jobs ADD COLUMN employer_id TEXT;
    ALTER TABLE jobs ADD COLUMN provided_type TEXT NOT NULL DEFAULT '';
    ALTER TABLE messages ADD COLUMN thread TEXT NOT NULL DEFAULT 'general';
    ALTER TABLE messages ADD COLUMN kind TEXT NOT NULL DEFAULT 'text';
    ALTER TABLE messages ADD COLUMN payload TEXT NOT NULL DEFAULT '';
    CREATE INDEX idx_msg_thread ON messages(thread, id);
    UPDATE pilot SET location_icao = home_icao WHERE location_icao = '';
    CREATE TABLE employment (
        employer_id TEXT PRIMARY KEY,
        hired_at TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'active',
        flights INTEGER NOT NULL DEFAULT 0,
        minutes REAL NOT NULL DEFAULT 0,
        earned REAL NOT NULL DEFAULT 0
    );
    CREATE TABLE applications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        employer_id TEXT NOT NULL,
        applied_at TEXT NOT NULL,
        status TEXT NOT NULL,
        message TEXT NOT NULL DEFAULT '',
        snapshot TEXT NOT NULL DEFAULT ''
    );
    """,
]
