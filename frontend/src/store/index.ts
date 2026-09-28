import { configureStore } from "@reduxjs/toolkit";
import authReducer from "./authSlice";
import greenPlanReducer from "./greenPlanSlice";

// Глобальное состояние: сессия и параметры GreenPlan. Сцена редактора сюда
// не идёт -- она большая, меняется на каждое перетаскивание и нужна одной
// странице (pages/editor/hooks).
export const store = configureStore({
  reducer: {
    auth: authReducer,
    greenPlan: greenPlanReducer,
  },
});

export type RootState = ReturnType<typeof store.getState>;
export type AppDispatch = typeof store.dispatch;
