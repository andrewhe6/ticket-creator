import { createApp } from "./app.js";
import { openDb } from "./db/index.js";

const db = openDb(process.env.DB_PATH ?? "todo.db");
const port = Number(process.env.PORT ?? 3000);
createApp(db).listen(port, () => console.log(`todo-app listening on :${port}`));
