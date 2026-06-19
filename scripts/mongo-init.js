// MongoDB init script — runs once when the container is first created.
// Creates the application database and a dedicated app user with least-privilege access.
// In Azure Cosmos DB this is not needed — the connection string carries credentials.

db = db.getSiblingDB(process.env.MONGO_INITDB_DATABASE || 'boise');

db.createUser({
  user: 'boise_app',
  pwd: process.env.APP_DB_PASSWORD || 'changeme_app',
  roles: [{ role: 'readWrite', db: process.env.MONGO_INITDB_DATABASE || 'boise' }],
});

print('MongoDB init complete: boise_app user created');
