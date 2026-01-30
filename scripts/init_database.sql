-- ================================================================
-- AIMScribe Database Schema
-- Run this script to initialize the PostgreSQL database
-- ================================================================

-- Create database (run as superuser)
-- CREATE DATABASE aimscribe_db;
-- CREATE USER aimscribe_user WITH PASSWORD 'your-password-here';
-- GRANT ALL PRIVILEGES ON DATABASE aimscribe_db TO aimscribe_user;

-- ================================================================
-- PATIENTS TABLE
-- Stores patient demographic information
-- ================================================================
CREATE TABLE IF NOT EXISTS patients (
    patient_id VARCHAR(50) PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    age INTEGER,
    gender VARCHAR(20),
    phone VARCHAR(20),
    address TEXT,
    blood_group VARCHAR(10),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ================================================================
-- HEALTH SCREENING TABLE
-- Stores patient baseline health data (vitals, medical history)
-- ================================================================
CREATE TABLE IF NOT EXISTS health_screening (
    id SERIAL PRIMARY KEY,
    patient_id VARCHAR(50) REFERENCES patients(patient_id),
    height_cm DECIMAL(5,2),
    weight_kg DECIMAL(5,2),
    bmi DECIMAL(4,2),
    blood_pressure VARCHAR(20),
    pulse_rate INTEGER,
    temperature DECIMAL(4,2),
    spo2 INTEGER,
    blood_sugar DECIMAL(6,2),

    -- Medical History
    diabetes BOOLEAN DEFAULT FALSE,
    hypertension BOOLEAN DEFAULT FALSE,
    heart_disease BOOLEAN DEFAULT FALSE,
    kidney_disease BOOLEAN DEFAULT FALSE,
    liver_disease BOOLEAN DEFAULT FALSE,
    asthma BOOLEAN DEFAULT FALSE,
    thyroid BOOLEAN DEFAULT FALSE,
    allergies TEXT,
    current_medications TEXT,

    screening_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ================================================================
-- SESSIONS TABLE
-- Stores doctor-patient consultation sessions
-- ================================================================
CREATE TABLE IF NOT EXISTS sessions (
    session_id VARCHAR(50) PRIMARY KEY,
    patient_id VARCHAR(50) REFERENCES patients(patient_id),
    doctor_id VARCHAR(50),
    status VARCHAR(20) DEFAULT 'active',  -- active, completed, cancelled
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    total_duration_seconds INTEGER DEFAULT 0,
    total_clips INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ================================================================
-- AUDIO CLIPS TABLE
-- Stores individual audio clips for each session
-- ================================================================
CREATE TABLE IF NOT EXISTS audio_clips (
    id SERIAL PRIMARY KEY,
    session_id VARCHAR(50) REFERENCES sessions(session_id),
    clip_number INTEGER NOT NULL,
    object_key VARCHAR(255),  -- MinIO object key
    duration_seconds DECIMAL(10,2),
    transcript TEXT,
    status VARCHAR(20) DEFAULT 'pending',  -- pending, transcribing, completed, failed
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    processed_at TIMESTAMP,

    UNIQUE(session_id, clip_number)
);

-- ================================================================
-- NER EXTRACTIONS TABLE
-- Stores extracted medical entities from transcripts
-- ================================================================
CREATE TABLE IF NOT EXISTS ner_extractions (
    id SERIAL PRIMARY KEY,
    session_id VARCHAR(50) REFERENCES sessions(session_id),
    extraction_version INTEGER DEFAULT 1,
    is_final BOOLEAN DEFAULT FALSE,

    -- Extracted Data (JSON)
    chief_complaints JSONB,
    symptoms JSONB,
    diagnosis JSONB,
    medications JSONB,
    tests JSONB,
    examination JSONB,
    follow_up JSONB,
    advice JSONB,
    referral JSONB,

    -- Full transcript used for extraction
    full_transcript TEXT,

    -- Metadata
    processing_time_seconds DECIMAL(10,2),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ================================================================
-- PREVIOUS VISITS TABLE
-- Archives completed sessions for follow-up reference
-- ================================================================
CREATE TABLE IF NOT EXISTS previous_visits (
    id SERIAL PRIMARY KEY,
    patient_id VARCHAR(50) REFERENCES patients(patient_id),
    session_id VARCHAR(50) REFERENCES sessions(session_id),
    doctor_id VARCHAR(50),
    visit_date TIMESTAMP,

    -- Archived NER Data
    chief_complaints JSONB,
    diagnosis JSONB,
    medications JSONB,
    tests JSONB,
    advice JSONB,

    -- Full Data
    full_ner_json JSONB,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ================================================================
-- CUMULATIVE TRANSCRIPTS TABLE
-- Stores the running transcript for each session
-- ================================================================
CREATE TABLE IF NOT EXISTS cumulative_transcripts (
    id SERIAL PRIMARY KEY,
    session_id VARCHAR(50) REFERENCES sessions(session_id) UNIQUE,
    full_transcript TEXT,
    last_clip_number INTEGER DEFAULT 0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ================================================================
-- INDEXES for Performance
-- ================================================================
CREATE INDEX IF NOT EXISTS idx_sessions_patient ON sessions(patient_id);
CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status);
CREATE INDEX IF NOT EXISTS idx_audio_clips_session ON audio_clips(session_id);
CREATE INDEX IF NOT EXISTS idx_audio_clips_status ON audio_clips(status);
CREATE INDEX IF NOT EXISTS idx_ner_session ON ner_extractions(session_id);
CREATE INDEX IF NOT EXISTS idx_ner_final ON ner_extractions(is_final);
CREATE INDEX IF NOT EXISTS idx_previous_visits_patient ON previous_visits(patient_id);
CREATE INDEX IF NOT EXISTS idx_health_screening_patient ON health_screening(patient_id);

-- ================================================================
-- SAMPLE DATA (Optional - for testing)
-- ================================================================
-- INSERT INTO patients (patient_id, name, age, gender) VALUES
--     ('P001', 'রহিম উদ্দিন', 45, 'Male'),
--     ('P002', 'ফাতেমা বেগম', 32, 'Female');

-- INSERT INTO health_screening (patient_id, height_cm, weight_kg, blood_pressure, diabetes, hypertension) VALUES
--     ('P001', 170, 75, '140/90', TRUE, TRUE),
--     ('P002', 155, 58, '120/80', FALSE, FALSE);

COMMENT ON TABLE patients IS 'Patient demographic information';
COMMENT ON TABLE health_screening IS 'Patient baseline health data and medical history';
COMMENT ON TABLE sessions IS 'Doctor-patient consultation sessions';
COMMENT ON TABLE audio_clips IS 'Individual audio clips with transcripts';
COMMENT ON TABLE ner_extractions IS 'Extracted medical entities from transcripts';
COMMENT ON TABLE previous_visits IS 'Archived sessions for follow-up reference';
COMMENT ON TABLE cumulative_transcripts IS 'Running transcript for each session';

-- Grant permissions
-- GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO aimscribe_user;
-- GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO aimscribe_user;
