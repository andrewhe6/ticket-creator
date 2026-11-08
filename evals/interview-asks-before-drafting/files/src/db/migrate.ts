import { openDb } from "./index.js";
openDb(process.env.DB_PATH ?? "todo.db").close();
console.log("migrations applied");
