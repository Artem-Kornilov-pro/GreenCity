import { createAsyncThunk, createSlice } from "@reduxjs/toolkit";
import * as authApi from "../auth";
import type { Session } from "../auth";
import { errorMessage } from "../lib/http";
import type { RootState } from "./index";

interface AuthState {
  session: Session | null;
  // Пока не завершилась проверка сохранённого refresh-токена при старте --
  // страницы, которым нужна сессия, не должны успеть редиректнуть гостя на
  // /login только потому, что проверка ещё не вернулась.
  initializing: boolean;
}

const initialState: AuthState = { session: null, initializing: true };

export interface Credentials {
  username: string;
  password: string;
}

// Сессия из localStorage, если сервер её ещё принимает.
export const restoreSession = createAsyncThunk("auth/restore", async (): Promise<Session | null> => {
  const saved = authApi.loadSession();
  if (!saved) return null;
  try {
    if (await authApi.whoAmI(saved)) return saved;
    authApi.clearSession();
    return null;
  } catch {
    // Сервер недоступен -- сессию не сбрасываем: токен проверится при
    // первом запросе к проектам.
    return saved;
  }
});

// rejectValue -- текст ошибки сервера («Неверное имя пользователя или
// пароль»), форма показывает его как есть.
export const login = createAsyncThunk<Session, Credentials, { rejectValue: string }>(
  "auth/login",
  async ({ username, password }, { rejectWithValue }) => {
    try {
      return await authApi.login(username, password);
    } catch (e) {
      return rejectWithValue(errorMessage(e));
    }
  },
);

export const register = createAsyncThunk<Session, Credentials, { rejectValue: string }>(
  "auth/register",
  async ({ username, password }, { rejectWithValue }) => {
    try {
      return await authApi.register(username, password);
    } catch (e) {
      return rejectWithValue(errorMessage(e));
    }
  },
);

export const logout = createAsyncThunk("auth/logout", (_: void, { getState }) => {
  const { session } = (getState() as RootState).auth;
  if (session) void authApi.logout(session.refreshToken); // отозвать на сервере, не дожидаясь ответа
  authApi.clearSession();
});

const authSlice = createSlice({
  name: "auth",
  initialState,
  reducers: {},
  extraReducers: (builder) => {
    builder
      .addCase(restoreSession.fulfilled, (state, action) => {
        state.session = action.payload;
        state.initializing = false;
      })
      .addCase(login.fulfilled, (state, action) => {
        state.session = action.payload;
      })
      .addCase(register.fulfilled, (state, action) => {
        state.session = action.payload;
      })
      .addCase(logout.pending, (state) => {
        state.session = null;
      });
  },
});

export default authSlice.reducer;

export const selectSession = (state: RootState) => state.auth.session;
export const selectAuthInitializing = (state: RootState) => state.auth.initializing;
