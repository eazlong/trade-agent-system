import axios from "axios"
import { getToken } from 'Utils/token'
import Router from 'next/router'

// export const HOST = process.env.NEXT_PUBLIC_API_URL || "8.217.168.196:3389"
export const HOST = process.env.NEXT_PUBLIC_API_URL || "app.tradelogx.me";
export const IMAGE_HOST = process.env.NEXT_PUBLIC_IMAGE_URL || "https://images.tradelogx.me/";
// export const HOST = process.env.NEXT_PUBLIC_API_URL || "127.0.0.1:8000"
const API_URL = `https://${HOST}/api/`;



export const apiClient = axios.create({
    baseURL: API_URL,
    headers: {
      "Content-Type": "application/json",
      // Authorization: `Bearer ${getToken()}`,
    },
  });
  
   
  apiClient.interceptors.request.use(
      (config) => {
          const token = getToken();
          if (token) {
              config.headers["Authorization"] =
                "Bearer " + token;        
          }
          return config;
      },
      (error) => {
          return Promise.reject(error);
      }
  );

  // 添加响应拦截器
  apiClient.interceptors.response.use(
      (response) => {
        if (
          (response.status === 403 ||
          response.status === 401)
        ) {
          // 跳转到登录页面
          // console.log(response.status);
          Router.push("/auth/login");
        }
        return response;
      },
      (error) => {
        // console.log(error.response.data);
        // console.log(error.response.status);
        // console.log(error.response.headers);
        // console.log(error.request);
        // console.log(error.message);
        // console.log(error.config);
          if (
            error.response &&
            (error.response.status === 403 ||
            error.response.status === 401)
          ) {
            // 跳转到登录页面
            Router.push("/auth/login");
          }
          return Promise.reject(error);
      }
  );

// 导出各个服务
// export * from './auth.service';
export * from './user.service';
export * from './assistant.service';
export * from './notify.service';
export * from './qtbot.service';
export * from './trade.service';
export * from './social.service';

export default API_URL; 