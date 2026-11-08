import { z } from "zod";

export const createTodoSchema = z.object({
  title: z.string().min(1).max(200),
});

export const updateTodoSchema = z.object({
  title: z.string().min(1).max(200).optional(),
  done: z.boolean().optional(),
});

export type CreateTodo = z.infer<typeof createTodoSchema>;
export type UpdateTodo = z.infer<typeof updateTodoSchema>;
