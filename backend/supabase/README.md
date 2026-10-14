# Database

PostgreSQL with pgvector. **Twenty migrations**, applied in order, plus a
verification query and a development seed.

## What each migration is for

Numbers are load-bearing: later files define functions over tables earlier
ones create, and several exist only to correct the one before. Those
corrections are kept rather than folded in, because a migration that had to
be fixed is the most useful thing in this directory.

| | |
|---|---|
| `0001` | Extensions and shared enums |
| `0002` | Tenancy: organizations, membership, and the RLS foundation |
| `0003` | The tender record, source documents, scrape observability |
| `0004` | Tenant-private tables: alert profiles, pipeline, notes |
| `0005` | The competitive core — hybrid semantic + keyword matching |
| `0006` | Inbound contact submissions from the landing page |
| `0007` | Close an RPC PostgREST exposed to unauthenticated callers |
| `0008` | **Finish what 0007 only appeared to do** |
| `0009` | Stop a transient failure erasing a successful extraction |
| `0010` | **Two corrections to 0009**, found by checking the result |
| `0011` | Age a tender's status against the clock |
| `0012` | Put that transition on a clock, because that is what it is about |
| `0013` | Score against the profile's own distribution, not a constant |
| `0014` | **0013 moved no profile off the floor** — `real` vs `numeric` |
| `0015` | Fan a profile out across tenders that already exist |
| `0016` | Deliver a match to the person it is for |
| `0017` | Give a new account somewhere to live, in the same transaction |
| `0018` | Do not alert on a tender that closes today |
| `0019` | Let a tender enter the corpus from its listing alone |
| `0020` | Put the lead-time skip on the clock it was reacting to |

Four of those twenty (`0008`, `0010`, `0014`, and half of `0012`) exist
because something looked applied and was not. Each ends in a query that
reads the end state back rather than trusting the statement above it — copy
that habit into any new migration.

## Applying

Numbered files under `migrations/`, in order. The order is load-bearing:
`0005` defines functions over tables that `0003` and `0004` create, and `0019` alters a trigger `0009` installed.

Either paste each file into the Supabase SQL Editor and run it, or use the
CLI:

```bash
supabase link --project-ref <ref>
supabase db push
```

Never edit a migration that has already been applied — add a new numbered
file.

## Verifying

```sql
-- paste verify.sql into the SQL Editor
```

Every row must read `OK`.

This exists because "Success. No rows returned" is what the SQL Editor
reports for a script that parsed and executed — and it is also what you see
when a policy fails to attach or an index quietly does not build. `verify.sql`
asserts the resulting shape instead: 9 tables, RLS enabled on all of them and
forced on the 5 tenant-scoped ones, 11 policies, the 7 project functions, both
HNSW indexes, the alert trigger, and the pgvector extension.

## Creating a user, an organization, and a membership

**Signing in is not enough to see alerts or a pipeline.** A new account
belongs to no organization, and every tenant-scoped policy keys on
membership, so those tabs stay empty until this is done. Tenders themselves
are visible to any signed-in user.

1. **Create the account.** Supabase dashboard → Authentication → Users → Add
   user. Do it here rather than through a sign-up form: this path skips email
   confirmation, so the account works immediately, and the free tier's mailer
   allows only a few messages an hour.

2. **Create the organization and the membership.** In the SQL Editor, with
   the address you just used:

```sql
with new_org as (
  insert into organizations (name, slug, sector)
  values ('Your Company', 'your-company', 'defense')
  returning id
)
insert into organization_members (organization_id, user_id, role)
select new_org.id, u.id, 'owner'
from new_org, auth.users u
where u.email = 'you@example.com';
```

3. **Confirm it took**, as that user rather than as the SQL Editor (which runs
   with privileges that bypass the policies you are trying to test): sign in
   to the dashboard. The "not a member of any organization" notice should be
   gone.

## Row level security

Ten tables, RLS enabled on all of them. Five are tenant-scoped — they carry
`organization_id` and RLS is additionally **forced**, so even the table owner
is subject to it:

| Table | Scoped by |
|---|---|
| `organizations` | membership |
| `organization_members` | membership |
| `alert_profiles` | `organization_id` |
| `tender_matches` | `organization_id` |
| `tender_pipeline` | `organization_id` |

Three of the rest — `tenders`, `tender_documents` and `tender_sources` —
hold shared records, carry no `organization_id`, and are readable by any
signed-in user. Note the role: those policies grant to **`authenticated`**,
not `anon`, so an anonymous caller reads nothing at all, including tenders.

`scrape_runs` was previously listed here as readable by any signed-in user.
**It is not, and never was.** Migration 0003 enables RLS on it and writes no
policy, which the comment there states is deliberate: it is operational
telemetry, and only the service role — which bypasses RLS — reads or writes
it. A UI that queried `scrape_runs` as a signed-in user would get zero rows
and no error, which is the failure this project keeps finding: the wrong
answer, arriving quietly, in a shape indistinguishable from the right one.

`leads` (migration 0006) is the tenth table and the only one an unauthenticated
visitor writes to. `anon` holds INSERT and nothing else — no SELECT policy
exists for any browser-facing role — so the landing page can submit a contact
form and cannot read back what it or anyone else submitted.

This was tested rather than assumed, against PostgreSQL 16 with pgvector: two
organizations with one alert profile each, then queried as each member. Each
saw only their own; a member could not insert into the other organization
even naming its id explicitly; a direct
`where organization_id = <the other org>` returned nothing; and a signed-in
user in no organization saw nothing.

The first run of that test returned nothing for all three users and looked
like a pass. It was invalid — the fixture inserts had failed on NOT NULL
columns, so the table was empty, and **an empty table is indistinguishable
from perfect isolation.** The rows were confirmed present before the result
was believed.

## seed.sql

Development data: two organizations, three alert profiles, and the tender
sources. **Do not run it against a project you care about.**

It deliberately inserts no tenders — those arrive from the scraper, not from
a fixture. It also sits outside `migrations/` on purpose, so `supabase db
reset` applies the migrations and then seeds separately, and demo rows can
never reach a production database by being replayed in migration order.
