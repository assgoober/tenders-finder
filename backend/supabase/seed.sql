-- seed.sql — local development data.
--
-- Run by `supabase db reset` for local development only. It is NOT a
-- migration and must never run against production: it inserts a fake
-- organization and alert profiles.
--
-- Embeddings are left null on purpose. Run the scraper service's
-- POST /profiles/embed to vectorize them with the configured provider —
-- inserting hand-written vectors here would put values from no embedding
-- model into the same space as real ones.

insert into organizations (id, name, slug, sector)
values
  ('00000000-0000-0000-0000-000000000001', 'Northwind Defense Systems', 'northwind-defense', 'defense'),
  ('00000000-0000-0000-0000-000000000002', 'Meridian Infrastructure Group', 'meridian-infra', 'municipal')
on conflict (id) do nothing;

-- Two organizations with overlapping interests, so RLS isolation is
-- actually testable: query as a member of one and confirm the other's
-- profiles are invisible.
insert into alert_profiles (organization_id, name, description, keywords, excluded_keywords, min_score)
values
  (
    '00000000-0000-0000-0000-000000000001',
    'Tactical Communications',
    'Ruggedized military communications equipment: handheld and vehicle-mounted VHF/UHF tactical radios, '
    'encrypted waveforms, battlefield networking, antennas and power systems. We manufacture to MIL-STD '
    'environmental and EMC requirements and hold AS9100D certification.',
    array['MIL-STD-810', 'MIL-STD-461', 'VHF', 'UHF', 'AES-256', 'tactical radio'],
    array['ammunition', 'catering'],
    0.72
  ),
  (
    '00000000-0000-0000-0000-000000000001',
    'Perimeter Security Systems',
    'Counter-UAS detection and tracking, perimeter intrusion detection, electro-optical sensors and '
    'command-and-control integration for critical infrastructure and military installations.',
    array['C-UAS', 'counter-UAS', 'perimeter', 'RF detection', 'radar'],
    array[]::text[],
    0.70
  ),
  (
    '00000000-0000-0000-0000-000000000002',
    'Water Network Rehabilitation',
    'Trenchless rehabilitation of potable water distribution networks: cured-in-place lining, pipe '
    'bursting, valve replacement and surface reinstatement. Certified to ISO 9001 and ISO 45001.',
    array['pipeline', 'relining', 'potable water', 'EN 805', 'trenchless'],
    array['desalination'],
    0.68
  )
on conflict do nothing;

insert into tender_sources (key, display_name, base_url, sector)
values ('sample-fixtures', 'Sample Fixtures (development)', 'https://fixtures.local', 'mixed')
on conflict (key) do nothing;
