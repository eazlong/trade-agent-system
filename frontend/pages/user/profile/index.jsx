import React, { useState, useEffect } from "react";
import { useForm } from "react-hook-form";
import { useToasts } from "react-toast-notifications";
import Layout from "Layout";
import { getUserProfile as fetchUserProfileAPI, updateUserProfile } from "services/user.service";
import { useSelector, useDispatch } from "react-redux";
import { getUserProfile as getUserProfileAction } from "store/modules/auth";

function MyProfile() {
    const { register, handleSubmit, setValue, formState: { errors } } = useForm();
    const { addToast } = useToasts();
    const [isLoading, setIsLoading] = useState(true);
    const [isSubmitting, setIsSubmitting] = useState(false);
    const { user } = useSelector((state) => state.auth);
    const dispatch = useDispatch();

    // 加载用户信息
    useEffect(() => {
        const loadUserProfile = async () => {
            try {
                setIsLoading(true);
                
                if (user) {
                    // 如果Redux中已有用户信息，直接使用
                    setValue('first_name', user.first_name || '');
                    setValue('last_name', user.last_name || '');
                    setValue('email', user.email || '');
                    setValue('username', user.username || '');
                    setIsLoading(false);
                } else {
                    // 否则从API获取
                    const response = await dispatch(getUserProfileAction());
                    if (response.payload && response.payload.status === 200) {
                        const data = response.payload.data;
                        setValue('first_name', data.first_name || '');
                        setValue('last_name', data.last_name || '');
                        setValue('email', data.email || '');
                        setValue('username', data.username || '');
                    } else {
                        addToast("获取用户信息失败", { appearance: "error", autoDismiss: true });
                    }
                    setIsLoading(false);
                }
            } catch (error) {
                console.error("获取用户信息出错:", error);
                addToast("获取用户信息出错", { appearance: "error", autoDismiss: true });
                setIsLoading(false);
            }
        };

        loadUserProfile();
    }, [setValue, addToast, user, dispatch]);

    // 提交表单更新用户信息
    const onSubmit = async (formData) => {
        try {
            setIsSubmitting(true);
            
            const { status, data } = await updateUserProfile({
                first_name: formData.first_name,
                last_name: formData.last_name,
                email: formData.email
            });
            
            if (status === 200) {
                // 更新成功后重新获取用户信息
                dispatch(getUserProfileAction());
                addToast("个人资料更新成功", { appearance: "success", autoDismiss: true });
            } else {
                let errorMessage = "更新失败";
                if (data && typeof data === 'object') {
                    // 处理错误信息
                    const errors = Object.entries(data)
                        .map(([field, msgs]) => `${field}: ${Array.isArray(msgs) ? msgs.join(', ') : msgs}`)
                        .join('; ');
                    errorMessage = errors || errorMessage;
                }
                addToast(errorMessage, { appearance: "error", autoDismiss: true });
            }
        } catch (error) {
            console.error("更新个人资料出错:", error);
            addToast("更新个人资料出错", { appearance: "error", autoDismiss: true });
        } finally {
            setIsSubmitting(false);
        }
    };

    return (
            <div className="container mx-auto px-4 py-8">
                <div className="max-w-3xl mx-auto bg-purple-100 rounded-lg shadow-md p-6">
                    <h1 className="text-2xl font-bold mb-6 text-gray-800">个人资料</h1>
                    
                    {isLoading ? (
                        <div className="flex justify-center items-center h-64">
                            <div className="animate-spin rounded-full h-12 w-12 border-t-2 border-b-2 border-purple-500"></div>
                        </div>
                    ) : (
                        <form onSubmit={handleSubmit(onSubmit)} className="space-y-6">
                            <div>
                                <label htmlFor="username" className="block text-sm font-medium text-gray-700 mb-1">
                                    用户名
                                </label>
                                <input
                                    type="text"
                                    id="username"
                                    disabled
                                    className="w-full px-3 py-2 border border-gray-300 rounded-md bg-gray-100 text-gray-500"
                                    {...register("username")}
                                />
                                <p className="mt-1 text-xs text-gray-500">用户名不可修改</p>
                            </div>
                            
                            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                                <div>
                                    <label htmlFor="first_name" className="block text-sm font-medium text-gray-700 mb-1">
                                        名字
                                    </label>
                                    <input
                                        type="text"
                                        id="first_name"
                                        className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-purple-500"
                                        {...register("first_name", { required: "名字不能为空" })}
                                    />
                                    {errors.first_name && (
                                        <p className="mt-1 text-sm text-red-600">{errors.first_name.message}</p>
                                    )}
                                </div>
                                
                                <div>
                                    <label htmlFor="last_name" className="block text-sm font-medium text-gray-700 mb-1">
                                        姓氏
                                    </label>
                                    <input
                                        type="text"
                                        id="last_name"
                                        className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-purple-500"
                                        {...register("last_name", { required: "姓氏不能为空" })}
                                    />
                                    {errors.last_name && (
                                        <p className="mt-1 text-sm text-red-600">{errors.last_name.message}</p>
                                    )}
                                </div>
                            </div>
                            
                            <div>
                                <label htmlFor="email" className="block text-sm font-medium text-gray-700 mb-1">
                                    电子邮箱
                                </label>
                                <input
                                    type="email"
                                    id="email"
                                    className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-purple-500"
                                    {...register("email", {
                                        required: "电子邮箱不能为空",
                                        pattern: {
                                            value: /^[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}$/i,
                                            message: "请输入有效的电子邮箱地址"
                                        }
                                    })}
                                />
                                {errors.email && (
                                    <p className="mt-1 text-sm text-red-600">{errors.email.message}</p>
                                )}
                            </div>
                            
                            <div className="flex justify-end">
                                <button
                                    type="submit"
                                    disabled={isSubmitting}
                                    className={`px-4 py-2 bg-purple-600 text-white rounded-md hover:bg-purple-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-purple-500 ${
                                        isSubmitting ? "opacity-50 cursor-not-allowed" : ""
                                    }`}
                                >
                                    {isSubmitting ? "保存中..." : "保存更改"}
                                </button>
                            </div>
                        </form>
                    )}
                </div>
            </div>
    );
}

export default MyProfile;