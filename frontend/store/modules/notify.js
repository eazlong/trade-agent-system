import { createAsyncThunk, createSlice } from '@reduxjs/toolkit';
import {
  getUserNotifyConfigs,
  getUserNotifyTemplates,
} from "services/notify.service";


export const fetchNotifyConfigs = createAsyncThunk(
    'notify/fetchNotifyConfigs',
    async () => {
        return await getUserNotifyConfigs()
    }
)

export const fetchNotifyConfigTemplates = createAsyncThunk(
  "notify/fetchNotifyConfigTemplates",
  async () => {
    return await getUserNotifyTemplates();
  }
);

const initialState = {
    notifyConfigs: [],
    templates: []
}

export const notifySlice = createSlice({
  name: "notify",
  initialState,
  reducers: {
    // standard rducer logic, with auto-generated action types per reducer
  },
  extraReducers: (builder)=>{
    builder.addCase( fetchNotifyConfigs.fulfilled,(state, action) => {
      state.notifyConfigs = action.payload;
    }).addCase(
      fetchNotifyConfigs.rejected,(state, action) => {
      state.notifyConfigs = [];
    }).addCase(
      fetchNotifyConfigTemplates.fulfilled,(state, action) => {
      state.templates = action.payload;
    }).addCase(
      fetchNotifyConfigTemplates.rejected,(state, action) => {
      state.templates = [];
    })
  }
});

// export const { addToCart, applyFilter } = productSlice.actions
export default notifySlice.reducer;