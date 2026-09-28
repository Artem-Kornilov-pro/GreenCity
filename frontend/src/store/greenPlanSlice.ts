import { createSlice, type PayloadAction } from "@reduxjs/toolkit";
import { DEFAULT_GREENPLAN_OPTIONS, type GreenPlanOptions } from "../api";
import type { RootState } from "./index";

// Последние параметры запуска GreenPlan -- живут, пока открыта вкладка:
// при переходе между проектами диалог открывается с ними же, перезагрузка
// страницы возвращает параметры по умолчанию.
interface GreenPlanSettingsState {
  options: GreenPlanOptions;
}

const initialState: GreenPlanSettingsState = { options: DEFAULT_GREENPLAN_OPTIONS };

const greenPlanSlice = createSlice({
  name: "greenPlan",
  initialState,
  reducers: {
    setGreenPlanOptions(state, action: PayloadAction<GreenPlanOptions>) {
      state.options = action.payload;
    },
  },
});

export const { setGreenPlanOptions } = greenPlanSlice.actions;
export default greenPlanSlice.reducer;

export const selectGreenPlanOptions = (state: RootState) => state.greenPlan.options;
