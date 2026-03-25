import Link from "next/link"
import { useRouter } from "next/router";
import React, { useRef } from "react";
import { useForm } from "react-hook-form";
import { useDispatch } from "react-redux";
import { useToasts } from "react-toast-notifications";
import { callRegisterAPI } from "services/auth.service";

const Register = () => {
    const router = useRouter()
    const { addToast } = useToasts()
    const dispatch = useDispatch()
    const { register, handleSubmit, watch, formState: { errors } } = useForm();

    const password = useRef({});
    password.current = watch("password", "");

    const onSubmit = async input => {
        const { status, data } = await callRegisterAPI(input);
        console.log(status, data)
        if (status === 401 || status === 400 || status === 403) {
            var message = ''
            for (let key in data) {
                message += `${data[key][0]}\n`
            }
            addToast(message, { appearance: 'error', })
        } else {
            addToast("Registration Successfully completed!", { appearance: 'success', autoDismiss: true, })
            router.push('/auth/login')
        }
    };

    return (
        <div className="min-h-screen py-2 flex flex-col justify-center sm:py-10">
            <div className="relative py-3 w-11/12 max-w-md mx-auto sm:max-w-xl">
                <div className="absolute inset-0 bg-gradient-to-r from-purple-300 to-purple-600 shadow-lg transform -skew-y-6 sm:skew-y-0 sm:-rotate-6 sm:rounded-3xl">
                </div>
                <div className="relative px-4 py-8 bg-purple-100 shadow-lg sm:rounded-3xl sm:p-10 justify-center">
                    <div className="max-w-md mx-auto">
                        <div className="border-b pb-3">
                            <h1 className="text-xl sm:text-2xl font-semibold">欢迎注册！</h1>
                            <span className="text-sm sm:text-base">注册以探索更多！</span>
                        </div>
                        <div className="divide-y divide-gray-200 mt-2">
                            <form onSubmit={handleSubmit(onSubmit)} autoComplete="off" noValidate
                                className="py-6 sm:py-8 text-base leading-6 space-y-4 text-gray-700 sm:text-lg sm:leading-7">

                                <div className="relative pb-3">
                                    <label htmlFor="email" className="pb-1 absolute left-0 -top-3.5 text-gray-600 text-sm peer-placeholder-shown:text-base peer-placeholder-shown:text-gray-440 peer-placeholder-shown:top-2 transition-all peer-focus:-top-3.5 peer-focus:text-gray-600 peer-focus:text-sm">邮箱地址</label>
                                    <input type="email"
                                        className="mt-1 peer h-10 sm:h-12 p-2 sm:p-3 w-full border-b border-gray-300 text-gray-900 focus:outline-none focus:border-purple-500"
                                        placeholder="请输入邮箱地址"
                                        {...register("email", { required: "The email is required", })}
                                    />
                                    {errors.email && <small className="text-red-500">{errors.email.message}</small>}
                                </div>

                                <div className="relative pb-3">
                                    <label htmlFor="username"
                                        className="pb-1 absolute left-0 -top-3.5 text-gray-600 text-sm peer-placeholder-shown:text-base peer-placeholder-shown:text-gray-440 peer-placeholder-shown:top-2 transition-all peer-focus:-top-3.5 peer-focus:text-gray-600 peer-focus:text-sm capitalize" >
                                        用户名
                                    </label>
                                    <input autoComplete="none" type="text"
                                        className="mt-1 peer h-10 sm:h-12 p-2 sm:p-3 w-full border-b border-gray-300 text-gray-900 focus:outline-none focus:border-purple-500"
                                        placeholder="请输入用户名"
                                        {...register("username", { required: "The username is required", })}
                                    />
                                    {errors.username && <small className="text-red-500">{errors.username.message}</small>}
                                </div>
                                
                                <div className="relative pb-3">
                                    <label htmlFor="password" className="pb-1 absolute left-0 -top-3.5 text-gray-600 text-sm peer-placeholder-shown:text-base peer-placeholder-shown:text-gray-440 peer-placeholder-shown:top-2 transition-all peer-focus:-top-3.5 peer-focus:text-gray-600 peer-focus:text-sm">
                                        密码
                                    </label>
                                    <input autoComplete="none"
                                        type="password"
                                        className="mt-1 peer h-10 sm:h-12 p-2 sm:p-3 w-full border-b border-gray-300 text-gray-900 focus:outline-none focus:border-purple-500"
                                        placeholder="请输入密码"
                                        {...register("password", { required: "The password is required", })}
                                    />
                                    {errors.password && <small className="text-red-500">{errors.password.message}</small>}
                                </div>
                                
                                <div className="relative pb-3">
                                    <label htmlFor="password" className="pb-1 absolute left-0 -top-3.5 text-gray-600 text-sm peer-placeholder-shown:text-base peer-placeholder-shown:text-gray-440 peer-placeholder-shown:top-2 transition-all peer-focus:-top-3.5 peer-focus:text-gray-600 peer-focus:text-sm">
                                        重复密码
                                    </label>
                                    <input autoComplete="none" type="password"
                                        className="mt-1 peer h-10 sm:h-12 p-2 sm:p-3 w-full border-b border-gray-300 text-gray-900 focus:outline-none focus:border-purple-500" 
                                        placeholder="请重复密码"
                                        {...register("re_password", {
                                            required: "The repeat password is required", validate: (value) =>
                                                value === password.current ||
                                                "The confirm passwords do not match",
                                        })}
                                    />
                                    {errors.re_password && <small className="text-red-500">{errors.re_password.message}</small>}
                                </div>
                                
                                <div className="relative flex flex-col sm:flex-row sm:items-center sm:justify-between">
                                    <button type="submit"
                                        className="mt-3 shadow-lg hover:shadow-2xl text-white bg-purple-500 hover:bg-purple-400 focus:ring-4 focus:ring-purple-400 font-medium rounded-lg text-base sm:text-lg px-5 sm:px-6 py-2 text-center flex items-center justify-center">
                                        注册
                                    </button>
                                    <Link href='/auth/login' className="text-sm mt-4 sm:mt-5 text-center sm:text-right underline font-bold">
                                        已有账号？
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

export default Register;