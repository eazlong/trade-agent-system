import { createAsyncThunk, createSlice } from '@reduxjs/toolkit'
import { callLoginAPI, fetchUserProfile } from 'services/auth.service';


// First, create the thunk
export const loginProcess = createAsyncThunk(
    'auth/login',
    async (data) => {
        return await callLoginAPI(data)
    }
)

export const getUserProfile = createAsyncThunk(
    'auth/getUserProfile',
    async () => {
        const response = await fetchUserProfile();
        if (!response) {
            return null;
        }
        const { status, data } = response;
        return { status, data };
    }
)

const initialState = {
    accessToken: null,
    error: null,
    user: null,
    loading: false,
}

export const authSlice = createSlice({
    name: 'auth',
    initialState,
    reducers: {
        saveToken: (state, action) => {
            state.accessToken = action.payload
        },
        clearToken: (state) => {
            state.accessToken = null
            state.error = null
            state.user = null
        },
        setUser: (state, action) => {
            state.user = action.payload
        },
    },
    extraReducers: (builder)=>{
        builder
        .addCase(loginProcess.fulfilled,(state, action) => {
            const { status, data } = action.payload
            if (status === 401 || status === 400) {
                state.accessToken = null
                state.error = data.detail
            } else {
                state.accessToken = data.access
                state.error = null
            }
        }).addCase(loginProcess.rejected,(state, action) => {
            state.accessToken = null
            state.error = "Something went wrong!"
        })
        .addCase(getUserProfile.pending, (state) => {
            state.loading = true
        })
        .addCase(getUserProfile.fulfilled, (state, action) => {
            if (!action.payload) {
                return
            }
            const { status, data } = action.payload;
            if (status === 200) {
                state.user = data;
                state.loading = false;
            } else {
                state.loading = false;
            }
        })
        .addCase(getUserProfile.rejected, (state) => {
            state.loading = false
        })
    },
})
// Action creators are generated for each case reducer function
export const { saveToken, clearToken, setUser } = authSlice.actions
export default authSlice.reducer