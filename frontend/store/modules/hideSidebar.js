import { createSlice } from '@reduxjs/toolkit'

const initialState = {
  hidden: false,
}

export const hideSidebarSlice = createSlice({
  name: 'hideSidebar',
  initialState,
  reducers: {
    hideSidebar: (state) => {
      state.hidden = true
      // 触发窗口resize事件以便页面布局能响应sidebar隐藏
      if (typeof window !== 'undefined') {
        setTimeout(() => {
          window.dispatchEvent(new Event('resize'))
        }, 0)
      }
    },
    showSidebar: (state) => {
      state.hidden = false
      // 触发窗口resize事件以便页面布局能响应sidebar显示
      if (typeof window !== 'undefined') {
        setTimeout(() => {
          window.dispatchEvent(new Event('resize'))
        }, 0)
      }
    },
    toggleSidebar: (state) => {
      state.hidden = !state.hidden
      console.log("toggleSidebar", state.hidden);
    
      if (typeof window !== 'undefined') {
        setTimeout(() => {
          window.dispatchEvent(new Event('resize'))
        }, 0)
      }
    },
  },
})

export const { hideSidebar, showSidebar, toggleSidebar } = hideSidebarSlice.actions
export default hideSidebarSlice.reducer
