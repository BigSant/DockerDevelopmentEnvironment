# Database migrations

Put reviewed application migrations in versions/. Connection settings come from
the container environment. Use make doctrine cmd=status or cmd=generate after
building the doctrine image. ORM diff requires an application schema provider.
