-- Emergency bed pipeline schema
-- hospitals          : Hospital base information (refreshed once a day)
-- bed_status         : Real-time available bed history (loaded every 10 minutes)
-- bed_status_latest  : Latest snapshot per hospital (queried by FastAPI)

CREATE TABLE IF NOT EXISTS hospitals (
    hpid            TEXT PRIMARY KEY,
    duty_name       TEXT NOT NULL,
    duty_div_name   TEXT,
    duty_addr       TEXT,
    duty_tel1       TEXT,
    duty_tel3       TEXT,
    duty_emcls      TEXT,
    duty_emcls_name TEXT,
    sido            TEXT,
    sigungu         TEXT,
    post_cdn        TEXT,
    latitude        DOUBLE PRECISION,
    longitude       DOUBLE PRECISION,
    raw             JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- KMA grid coordinates (for joining weather). Added via ALTER so it also applies to existing deployments.
ALTER TABLE hospitals ADD COLUMN IF NOT EXISTS nx INTEGER;
ALTER TABLE hospitals ADD COLUMN IF NOT EXISTS ny INTEGER;

CREATE INDEX IF NOT EXISTS hospitals_region_idx ON hospitals (sido, sigungu);
CREATE INDEX IF NOT EXISTS hospitals_coord_idx ON hospitals (latitude, longitude);
CREATE INDEX IF NOT EXISTS hospitals_grid_idx ON hospitals (nx, ny);

CREATE TABLE IF NOT EXISTS bed_status (
    hpid         TEXT NOT NULL,
    hvidate      TIMESTAMPTZ NOT NULL,   -- Time the hospital last updated the information
    duty_name    TEXT,
    sido         TEXT,
    hvec         INTEGER,                -- ER general beds
    hvoc         INTEGER,                -- Operating rooms
    hvcc         INTEGER,                -- Neurosurgical ICU
    hvncc        INTEGER,                -- Neonatal ICU
    hvccc        INTEGER,                -- Thoracic surgery ICU
    hvicc        INTEGER,                -- General ICU
    hvgc         INTEGER,                -- Inpatient beds (general)
    hvs01        INTEGER,                -- ER reference bed count
    hv1          INTEGER,                -- Detailed available beds hv1 ~ hv12
    hv2          INTEGER,                -- (Item names follow the Public Data Portal docs;
    hv3          INTEGER,                --  original values are also preserved as-is in raw JSONB)
    hv4          INTEGER,
    hv5          INTEGER,
    hv6          INTEGER,
    hv7          INTEGER,
    hv8          INTEGER,
    hv9          INTEGER,
    hv10         INTEGER,
    hv11         INTEGER,
    hv12         INTEGER,
    hvctayn      BOOLEAN,                -- CT available
    hvmriayn     BOOLEAN,                -- MRI available
    hvangioayn   BOOLEAN,                -- Angiography available
    hvventiayn   BOOLEAN,                -- Ventilator available
    hvamyn       BOOLEAN,                -- Ambulance available
    hvdnm        TEXT,                   -- On-duty doctor
    raw          JSONB NOT NULL DEFAULT '{}'::jsonb,
    collected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (hpid, hvidate)
);

CREATE INDEX IF NOT EXISTS bed_status_hvidate_idx ON bed_status (hvidate DESC);
CREATE INDEX IF NOT EXISTS bed_status_hpid_hvidate_idx ON bed_status (hpid, hvidate DESC);

CREATE TABLE IF NOT EXISTS bed_status_latest (
    hpid         TEXT PRIMARY KEY,
    hvidate      TIMESTAMPTZ NOT NULL,
    duty_name    TEXT,
    sido         TEXT,
    hvec         INTEGER,
    hvoc         INTEGER,
    hvcc         INTEGER,
    hvncc        INTEGER,
    hvccc        INTEGER,
    hvicc        INTEGER,
    hvgc         INTEGER,
    hvs01        INTEGER,
    hv1          INTEGER,
    hv2          INTEGER,
    hv3          INTEGER,
    hv4          INTEGER,
    hv5          INTEGER,
    hv6          INTEGER,
    hv7          INTEGER,
    hv8          INTEGER,
    hv9          INTEGER,
    hv10         INTEGER,
    hv11         INTEGER,
    hv12         INTEGER,
    hvctayn      BOOLEAN,
    hvmriayn     BOOLEAN,
    hvangioayn   BOOLEAN,
    hvventiayn   BOOLEAN,
    hvamyn       BOOLEAN,
    hvdnm        TEXT,
    raw          JSONB NOT NULL DEFAULT '{}'::jsonb,
    collected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS bed_status_latest_sido_idx ON bed_status_latest (sido);
CREATE INDEX IF NOT EXISTS bed_status_latest_hvec_idx ON bed_status_latest (hvec DESC);

-- hv1 ~ hv12 were added later, so attach them to tables that already exist as well.
-- (CREATE TABLE IF NOT EXISTS does not change the columns of an existing table)
DO $$
DECLARE
    target_table TEXT;
    index_number INTEGER;
BEGIN
    FOREACH target_table IN ARRAY ARRAY['bed_status', 'bed_status_latest'] LOOP
        FOR index_number IN 1..12 LOOP
            EXECUTE format(
                'ALTER TABLE %I ADD COLUMN IF NOT EXISTS %I INTEGER',
                target_table,
                'hv' || index_number
            );
        END LOOP;
    END LOOP;
END $$;

-- ---------------------------------------------------------------------------
-- Weather (KMA short-term forecast service) — loaded directly, without going through Event Hub
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS weather_current (
    nx            INTEGER NOT NULL,
    ny            INTEGER NOT NULL,
    base_datetime TIMESTAMPTZ NOT NULL,   -- Observation release time
    t1h           REAL,                   -- Temperature (°C)
    rn1           REAL,                   -- 1-hour precipitation (mm)
    reh           REAL,                   -- Humidity (%)
    wsd           REAL,                   -- Wind speed (m/s)
    vec           REAL,                   -- Wind direction (deg)
    pty           INTEGER,                -- Precipitation type: 0 none, 1 rain, 2 rain/snow, 3 snow, 4 shower
    raw           JSONB NOT NULL DEFAULT '{}'::jsonb,
    collected_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (nx, ny, base_datetime)
);

CREATE INDEX IF NOT EXISTS weather_current_recent_idx
    ON weather_current (nx, ny, base_datetime DESC);

CREATE TABLE IF NOT EXISTS weather_forecast (
    nx             INTEGER NOT NULL,
    ny             INTEGER NOT NULL,
    base_datetime  TIMESTAMPTZ NOT NULL,  -- Forecast release time
    fcst_datetime  TIMESTAMPTZ NOT NULL,  -- Forecast target time
    tmp            REAL,                  -- Temperature (°C)
    reh            REAL,                  -- Humidity (%)
    wsd            REAL,                  -- Wind speed (m/s)
    pop            INTEGER,               -- Probability of precipitation (%)
    pty            INTEGER,               -- Precipitation type
    sky            INTEGER,               -- Sky condition: 1 clear, 3 mostly cloudy, 4 overcast
    pcp            TEXT,                  -- 1-hour precipitation (string, e.g. "강수없음")
    raw            JSONB NOT NULL DEFAULT '{}'::jsonb,
    collected_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (nx, ny, base_datetime, fcst_datetime)
);

CREATE INDEX IF NOT EXISTS weather_forecast_lookup_idx
    ON weather_forecast (nx, ny, fcst_datetime, base_datetime DESC);