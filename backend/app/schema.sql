-- 응급 병상 파이프라인 스키마
-- hospitals          : 기관 기본정보 (하루 1회 갱신)
-- bed_status         : 실시간 가용 병상 이력 (10분 주기 적재)
-- bed_status_latest  : 기관별 최신 스냅샷 (FastAPI 조회 대상)

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

-- 기상청 격자 좌표 (날씨 조인용). 기존 배포본에도 적용되도록 ALTER 로 추가한다.
ALTER TABLE hospitals ADD COLUMN IF NOT EXISTS nx INTEGER;
ALTER TABLE hospitals ADD COLUMN IF NOT EXISTS ny INTEGER;

CREATE INDEX IF NOT EXISTS hospitals_region_idx ON hospitals (sido, sigungu);
CREATE INDEX IF NOT EXISTS hospitals_coord_idx ON hospitals (latitude, longitude);
CREATE INDEX IF NOT EXISTS hospitals_grid_idx ON hospitals (nx, ny);

CREATE TABLE IF NOT EXISTS bed_status (
    hpid         TEXT NOT NULL,
    hvidate      TIMESTAMPTZ NOT NULL,   -- 병원이 입력한 정보 갱신 시각
    duty_name    TEXT,
    sido         TEXT,
    hvec         INTEGER,                -- 응급실 일반 병상
    hvoc         INTEGER,                -- 수술실
    hvcc         INTEGER,                -- 신경외과 중환자실
    hvncc        INTEGER,                -- 신생아 중환자실
    hvccc        INTEGER,                -- 흉부외과 중환자실
    hvicc        INTEGER,                -- 일반 중환자실
    hvgc         INTEGER,                -- 입원실(일반)
    hvs01        INTEGER,                -- 응급실 기준 병상 수
    hv1          INTEGER,                -- 세부 가용 병상 hv1 ~ hv12
    hv2          INTEGER,                -- (항목명은 공공데이터포털 문서 기준,
    hv3          INTEGER,                --  원본 값은 raw JSONB에도 그대로 보존)
    hv4          INTEGER,
    hv5          INTEGER,
    hv6          INTEGER,
    hv7          INTEGER,
    hv8          INTEGER,
    hv9          INTEGER,
    hv10         INTEGER,
    hv11         INTEGER,
    hv12         INTEGER,
    hvctayn      BOOLEAN,                -- CT 가용 여부
    hvmriayn     BOOLEAN,                -- MRI 가용 여부
    hvangioayn   BOOLEAN,                -- 혈관촬영기 가용 여부
    hvventiayn   BOOLEAN,                -- 인공호흡기 가용 여부
    hvamyn       BOOLEAN,                -- 구급차 가용 여부
    hvdnm        TEXT,                   -- 당직의
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

-- hv1 ~ hv12 는 나중에 추가된 컬럼이라 이미 만들어진 테이블에도 붙여 준다.
-- (CREATE TABLE IF NOT EXISTS 는 기존 테이블의 컬럼을 바꾸지 않는다)
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
-- 날씨 (기상청 단기예보 조회서비스) — Event Hub 경유 없이 직접 적재
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS weather_current (
    nx            INTEGER NOT NULL,
    ny            INTEGER NOT NULL,
    base_datetime TIMESTAMPTZ NOT NULL,   -- 관측 발표 시각
    t1h           REAL,                   -- 기온(°C)
    rn1           REAL,                   -- 1시간 강수량(mm)
    reh           REAL,                   -- 습도(%)
    wsd           REAL,                   -- 풍속(m/s)
    vec           REAL,                   -- 풍향(deg)
    pty           INTEGER,                -- 강수형태 0없음 1비 2비/눈 3눈 4소나기
    raw           JSONB NOT NULL DEFAULT '{}'::jsonb,
    collected_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (nx, ny, base_datetime)
);

CREATE INDEX IF NOT EXISTS weather_current_recent_idx
    ON weather_current (nx, ny, base_datetime DESC);

CREATE TABLE IF NOT EXISTS weather_forecast (
    nx             INTEGER NOT NULL,
    ny             INTEGER NOT NULL,
    base_datetime  TIMESTAMPTZ NOT NULL,  -- 예보 발표 시각
    fcst_datetime  TIMESTAMPTZ NOT NULL,  -- 예보 대상 시각
    tmp            REAL,                  -- 기온(°C)
    reh            REAL,                  -- 습도(%)
    wsd            REAL,                  -- 풍속(m/s)
    pop            INTEGER,               -- 강수확률(%)
    pty            INTEGER,               -- 강수형태
    sky            INTEGER,               -- 하늘상태 1맑음 3구름많음 4흐림
    pcp            TEXT,                  -- 1시간 강수량("강수없음" 등 문자열)
    raw            JSONB NOT NULL DEFAULT '{}'::jsonb,
    collected_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (nx, ny, base_datetime, fcst_datetime)
);

CREATE INDEX IF NOT EXISTS weather_forecast_lookup_idx
    ON weather_forecast (nx, ny, fcst_datetime, base_datetime DESC);
