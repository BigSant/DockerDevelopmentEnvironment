# Setup

One shared checkout that builds and runs an isolated Docker stack per PHP project; projects keep only their settings and sources.

## Language

**Database client**:
Setup's single way of talking to a project's database from the host: it runs SQL, loads a dump and dumps data, choosing the account and the installed client itself.
_Avoid_: DB adapter, mysql_query, database port
