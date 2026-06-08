-- Allow unknown Missing Middle opt-out status to remain null instead of defaulting false.
-- Screening logic treats null as unknown and applies conservative exemption handling.

ALTER TABLE lla.millage
    ALTER COLUMN opted_out_middle DROP DEFAULT;

ALTER TABLE lla.millage
    ALTER COLUMN opted_out_middle DROP NOT NULL;
