import { configureStore } from '@reduxjs/toolkit'
// import commonReducer from './modules/common'
import authReducer from './modules/auth'
import hideSidebarReducer from './modules/hideSidebar'
// import productsReducer from './modules/product'
// import notifyReducer from './modules/notify'
// import editorContentReducer from './modules/editorContent'

export const store = configureStore({
  reducer: {
    auth: authReducer,
    sidebar: hideSidebarReducer,
    // common: commonReducer,
    // products: productsReducer,
    // notifyConfigs: notifyReducer,
    // editorContent: editorContentReducer,
  },
});