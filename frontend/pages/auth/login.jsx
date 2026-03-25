import Link from "next/link"
import { useRouter } from 'next/router';
import React, { useState, useEffect } from "react";
import { useForm } from "react-hook-form";
import { useToasts } from 'react-toast-notifications';
import { callLoginAPI } from 'services/auth.service';
import { saveToken, getUserProfile } from 'store/modules/auth';
import { removeToken, setToken } from 'Utils/token';
import { useDispatch } from 'react-redux';
import axios from "axios";
import API_URL from 'services';

const Login = () => {
    const router = useRouter()
    const { addToast } = useToasts()
    const { register, handleSubmit, setValue, formState: { errors } } = useForm();
    const dispatch = useDispatch();
    const [captchaRequired, setCaptchaRequired] = useState(false);
    const [captchaToken, setCaptchaToken] = useState('');
    const [isSubmitting, setIsSubmitting] = useState(false);
    const [captchaImageUrl, setCaptchaImageUrl] = useState('');
    const [captchaKey, setCaptchaKey] = useState('');

    // Load reCAPTCHA script
    useEffect(() => {
        // In a real implementation, you would load the reCAPTCHA script here
        // For example:
        // const script = document.createElement('script');
        // script.src = 'https://www.google.com/recaptcha/api.js';
        // script.async = true;
        // document.body.appendChild(script);
        // return () => {
        //     document.body.removeChild(script);
        // };
    }, []);

    const handleCaptchaChange = (token) => {
        setCaptchaToken(token);
        setValue('captcha_token', token);
    };

    const loadCaptcha = async () => {
        try {
            const response = await axios.get(`${API_URL}captcha/`);
            if (response.data) {
                setCaptchaImageUrl(response.data.image_url);
                setCaptchaKey(response.data.captcha_key);
                setValue('captcha_key', response.data.captcha_key);
            }
        } catch (error) {
            console.error('Failed to load captcha:', error);
        }
    };

    useEffect(() => {
        if (captchaRequired) {
            loadCaptcha();
        }
    }, [captchaRequired]);

    const refreshCaptcha = () => {
        loadCaptcha();
    };

    const onSubmit = async (input) => {
        try {
            setIsSubmitting(true);
            
            
            // Add captcha token if required
            if (captchaRequired) {
                input.captcha_key = captchaKey;
            }
            
            const { status, data } = (await callLoginAPI(input)) || {};
            
            if (status == null || data == null) {
                setIsSubmitting(false);
                return;
            }

            if (status === 401 || status === 400) {
                removeToken(null);
                
                // Check if CAPTCHA is required
                if (data.captcha_required) {
                    setCaptchaRequired(true);
                    loadCaptcha();
                    addToast("请输入验证码继续", { appearance: "warning", autoDismiss: true });
                } else {
                    addToast(data.detail, { appearance: "error", autoDismiss: true });
                }
            } else {
              setToken(data.access);
              // Save username to localStorage for future use
              if (input.username) {
                localStorage.setItem("username", input.username);
              }

              dispatch(saveToken(data.access));
              
              // Fetch user profile after successful login
              dispatch(getUserProfile()).then(() => {
                addToast("登录成功!", {
                  appearance: "success",
                  autoDismiss: true,
                });
                router.push("/");
              });
            }
        } catch (error) {
            console.log(error);
            
            addToast("网络错误，请稍后重试", {
                appearance: "error",
                autoDismiss: true,
            });
        } finally {
            setIsSubmitting(false);
        }
    };

    return (
        <div className="min-h-screen py-2 flex flex-col justify-center sm:py-10">
            <div className="relative py-3 w-11/12 max-w-md mx-auto sm:max-w-xl">
                <div
                    className="absolute inset-0 bg-gradient-to-r from-purple-300 to-purple-600 shadow-lg transform -skew-y-6 sm:skew-y-0 sm:-rotate-6 sm:rounded-3xl">
                </div>
                <div className="relative px-4 py-8 bg-purple-100 shadow-lg sm:rounded-3xl sm:p-10 justify-center">
                    <div className="max-w-md mx-auto">
                        <div>
                            <h1 className="text-xl sm:text-2xl font-semibold">欢迎回来！</h1>
                            <span className="text-sm sm:text-base">登录以探索更多！</span>
                        </div>
                        <div className="divide-y divide-gray-200">
                            <form onSubmit={handleSubmit(onSubmit)} autoComplete="off" noValidate
                                className="py-6 sm:py-8 text-base leading-6 space-y-4 text-gray-700 sm:text-lg sm:leading-7">

                                <div className="relative pb-3">
                                    <label htmlFor="username" className="pb-1 absolute left-0 -top-3.5 text-gray-600 text-sm peer-placeholder-shown:text-base peer-placeholder-shown:text-gray-440 peer-placeholder-shown:top-2 transition-all peer-focus:-top-3.5 peer-focus:text-gray-600 peer-focus:text-sm capitalize"
                                    >
                                        用户名</label>
                                    <input type="text"
                                        className="mt-3 peer placeholder-transparent h-10 sm:h-12 p-2 sm:p-3 w-full border-b-2 border-gray-300 text-gray-900 focus:outline-none focus:border-purple-500"
                                        placeholder="请输入用户名"
                                        {...register("username", { required: "The username is required", })}
                                    />
                                    {errors.username && <small className="text-red-500">{errors.username.message}</small>}
                                </div>
                                <div className="relative">
                                    <label htmlFor="password" className="pb-1 absolute left-0 -top-3.5 text-gray-600 text-sm peer-placeholder-shown:text-base peer-placeholder-shown:text-gray-440 peer-placeholder-shown:top-2 transition-all peer-focus:-top-3.5 peer-focus:text-gray-600 peer-focus:text-sm">密码</label>
                                    <input autoComplete="none" type="password"
                                        className="mt-3 peer placeholder-transparent h-10 sm:h-12 p-2 sm:p-3 w-full border-b-2 border-gray-300 text-gray-900 focus:outline-none focus:border-purple-500"
                                        placeholder="请输入密码"
                                        {...register("password", { required: "The password is required", })}
                                    />
                                    {errors.password && <small className="text-red-500">{errors.password.message}</small>}
                                </div>
                                
                                {/* CAPTCHA Field - only shown when required */}
                                {captchaRequired && (
                                    <div className="relative">
                                        <div className="my-4">
                                            <label htmlFor="captcha_value" className="block text-sm font-medium text-gray-700 mb-1">
                                                请输入验证码
                                            </label>
                                            <div className="flex items-center">
                                                {captchaImageUrl && (
                                                    <img 
                                                        src={captchaImageUrl} 
                                                        alt="验证码" 
                                                        className="h-10 border border-gray-300 rounded mr-2"
                                                    />
                                                )}
                                                <button
                                                    type="button"
                                                    onClick={refreshCaptcha}
                                                    className="text-sm text-purple-600 hover:text-purple-800"
                                                >
                                                    刷新
                                                </button>
                                            </div>
                                            <input
                                                type="text"
                                                id="captcha_value"
                                                className="mt-1 w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-purple-500"
                                                placeholder="输入验证码"
                                                {...register("captcha_value", { required: captchaRequired })}
                                            />
                                            {errors.captcha_value && <small className="text-red-500">请输入验证码</small>}
                                        </div>
                                    </div>
                                )}
                                
                                <div className="flex justify-end">
                                    <Link href="/auth/forgot-password" className="text-sm text-purple-600 hover:text-purple-800">
                                        忘记密码？
                                    </Link>
                                </div>
                                
                                <div className="relative flex flex-col sm:flex-row sm:items-center sm:justify-between">
                                    <button type="submit"
                                        disabled={isSubmitting}
                                        className={`mt-3 shadow-lg hover:shadow-2xl text-white bg-purple-500 hover:bg-purple-400 focus:ring-4 focus:ring-purple-400 font-medium rounded-lg text-base sm:text-lg px-5 sm:px-6 py-2 text-center flex items-center justify-center ${isSubmitting ? 'opacity-50 cursor-not-allowed' : ''}`}
                                    >
                                        {isSubmitting ? '登录中...' : '登录'}
                                    </button>

                                    <Link href='/auth/register'
                                    className="text-sm mt-4 sm:mt-5 text-center sm:text-right underline font-bold"
                                    >
                                        还没有账号？
                                    </Link>
                                </div>
                            </form>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    );
}

export default Login;