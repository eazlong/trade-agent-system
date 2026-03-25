import Link from "next/link"
import { useRouter } from 'next/router';
import React, { useState, useRef } from "react";
import { useForm } from "react-hook-form";
import { useToasts } from 'react-toast-notifications';
import axios from "axios";
import API_URL from 'services';

const ResetPassword = () => {
    const router = useRouter();
    const { uid, token } = router.query;
    const { addToast } = useToasts();
    const { register, handleSubmit, watch, formState: { errors } } = useForm();
    const [isSubmitting, setIsSubmitting] = useState(false);
    
    const password = useRef({});
    password.current = watch("new_password", "");

    const onSubmit = async (input) => {
        try {
            setIsSubmitting(true);
            const resetData = {
                uid,
                token,
                new_password: input.new_password
            };
            
            const { status, data } = await axios.post(API_URL + 'auth/password/reset/confirm/', resetData);
            
            if (status === 200) {
                addToast("Password has been reset successfully.", {
                    appearance: "success",
                    autoDismiss: true,
                });
                router.push("/auth/login");
            } else {
                addToast("An error occurred. Please try again.", {
                    appearance: "error",
                    autoDismiss: true,
                });
            }
        } catch (error) {
            console.log(error);
            let errorMessage = "An error occurred. Please try again.";
            
            if (error.response && error.response.data) {
                if (error.response.data.uid) {
                    errorMessage = "Invalid reset link. Please request a new one.";
                } else if (error.response.data.token) {
                    errorMessage = "This reset link has expired. Please request a new one.";
                }
            }
            
            addToast(errorMessage, {
                appearance: "error",
                autoDismiss: true,
            });
        } finally {
            setIsSubmitting(false);
        }
    };

    // If uid or token is not available yet (during initial SSR), show loading
    if (!uid || !token) {
        return (
            <div className="min-h-screen flex items-center justify-center">
                <p className="text-xl">Loading...</p>
            </div>
        );
    }

    return (
        <div className="min-h-screen py-2 flex flex-col justify-center sm:py-10">
            <div className="relative py-3 w-11/12 max-w-md mx-auto sm:max-w-xl">
                <div className="absolute inset-0 bg-gradient-to-r from-purple-300 to-purple-600 shadow-lg transform -skew-y-6 sm:skew-y-0 sm:-rotate-6 sm:rounded-3xl">
                </div>
                <div className="relative px-4 py-8 bg-purple-100 shadow-lg sm:rounded-3xl sm:p-10 justify-center">
                    <div className="max-w-md mx-auto">
                        <div>
                            <h1 className="text-xl sm:text-2xl font-semibold">Reset Password</h1>
                            <span className="text-sm sm:text-base">Enter your new password</span>
                        </div>
                        <div className="divide-y divide-gray-200">
                            <form onSubmit={handleSubmit(onSubmit)} autoComplete="off" noValidate
                                className="py-6 sm:py-8 text-base leading-6 space-y-4 text-gray-700 sm:text-lg sm:leading-7">

                                <div className="relative pb-3">
                                    <label htmlFor="new_password" className="pb-1 absolute left-0 -top-3.5 text-gray-600 text-sm peer-placeholder-shown:text-base peer-placeholder-shown:text-gray-440 peer-placeholder-shown:top-2 transition-all peer-focus:-top-3.5 peer-focus:text-gray-600 peer-focus:text-sm">New Password</label>
                                    <input type="password"
                                        className="mt-3 peer placeholder-transparent h-10 sm:h-12 p-2 sm:p-3 w-full border-b-2 border-gray-300 text-gray-900 focus:outline-none focus:border-purple-500"
                                        placeholder="New Password"
                                        {...register("new_password", { 
                                            required: "New password is required",
                                            minLength: {
                                                value: 8,
                                                message: "Password must be at least 8 characters"
                                            }
                                        })}
                                    />
                                    {errors.new_password && <small className="text-red-500">{errors.new_password.message}</small>}
                                </div>
                                
                                <div className="relative pb-3">
                                    <label htmlFor="confirm_password" className="pb-1 absolute left-0 -top-3.5 text-gray-600 text-sm peer-placeholder-shown:text-base peer-placeholder-shown:text-gray-440 peer-placeholder-shown:top-2 transition-all peer-focus:-top-3.5 peer-focus:text-gray-600 peer-focus:text-sm">Confirm Password</label>
                                    <input type="password"
                                        className="mt-3 peer placeholder-transparent h-10 sm:h-12 p-2 sm:p-3 w-full border-b-2 border-gray-300 text-gray-900 focus:outline-none focus:border-purple-500"
                                        placeholder="Confirm Password"
                                        {...register("confirm_password", { 
                                            required: "Please confirm your password",
                                            validate: value => value === password.current || "The passwords do not match"
                                        })}
                                    />
                                    {errors.confirm_password && <small className="text-red-500">{errors.confirm_password.message}</small>}
                                </div>
                                
                                <div className="relative flex flex-col sm:flex-row sm:items-center sm:justify-between">
                                    <button type="submit"
                                        disabled={isSubmitting}
                                        className={`mt-3 shadow-lg hover:shadow-2xl text-white bg-purple-500 hover:bg-purple-400 focus:ring-4 focus:ring-purple-400 font-medium rounded-lg text-base sm:text-lg px-5 sm:px-6 py-2 text-center flex items-center justify-center ${isSubmitting ? 'opacity-50 cursor-not-allowed' : ''}`}
                                    >
                                        {isSubmitting ? 'Resetting...' : 'Reset Password'}
                                    </button>

                                    <Link href='/auth/login'
                                    className="text-sm mt-4 sm:mt-5 text-center sm:text-right underline font-bold"
                                    >
                                        Back to Login
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

export default ResetPassword; 