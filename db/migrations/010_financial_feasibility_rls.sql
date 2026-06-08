-- Defense-in-depth for newly added internal financial feasibility tables.
-- No anon/auth policies are added in this backend-only pass.

ALTER TABLE lla.rent_limits ENABLE ROW LEVEL SECURITY;
ALTER TABLE lla.parcel_scenarios ENABLE ROW LEVEL SECURITY;
ALTER TABLE lla.parcel_notes ENABLE ROW LEVEL SECURITY;
ALTER TABLE lla.parcel_review_status ENABLE ROW LEVEL SECURITY;
