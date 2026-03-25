import React, { useState, useRef } from "react";
import { useForm } from "react-hook-form";
import { useToasts } from 'react-toast-notifications';
import axios from "axios";
import API_URL from 'services';
import { getToken } from 'Utils/token';
import Layout from "Layout";

const ChangePassword = () => {
    const { addToast } = useToasts();
    const { register, handleSubmit, watch, reset, formState: { errors } } = useForm();
    const [isSubmitting, setIsSubmitting] = useState(false);
    
    const password = useRef({});
    password.current = watch("new_password", "");

    const onSubmit = async (input) => {
        try {
            setIsSubmitting(true);
            const token = getToken();
            
            const { status, data } = await axios.post(
                API_URL + 'auth/password/change/', 
                {
                    old_password: input.old_password,
                    new_password: input.new_password
                },
                {
                    headers: {
                        Authorization: `Bearer ${token}`
                    }
                }
            );
            
            if (status === 200) {
                addToast("Password changed successfully.", {
                    appearance: "success",
                    autoDismiss: true,
                });
                reset(); // Reset the form
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
                if (error.response.data.old_password) {
                    errorMessage = "Current password is incorrect.";
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

    return (
            <div className="container mx-auto px-4 py-8">
                <div className="max-w-md mx-auto bg-purple-100 rounded-lg shadow-md p-6">
                    <h1 className="text-2xl font-bold mb-6">Change Password</h1>
                    
                    <form onSubmit={handleSubmit(onSubmit)} className="space-y-6">
                        <div>
                            <label htmlFor="old_password" className="block text-sm font-medium text-gray-700 mb-1">
                                Current Password
                            </label>
                            <input
                                type="password"
                                id="old_password"
                                className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-purple-500"
                                {...register("old_password", { required: "Current password is required" })}
                            />
                            {errors.old_password && (
                                <p className="text-red-500 text-sm mt-1">{errors.old_password.message}</p>
                            )}
                        </div>
                        
                        <div>
                            <label htmlFor="new_password" className="block text-sm font-medium text-gray-700 mb-1">
                                New Password
                            </label>
                            <input
                                type="password"
                                id="new_password"
                                className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-purple-500"
                                {...register("new_password", {
                                    required: "New password is required",
                                    minLength: {
                                        value: 8,
                                        message: "Password must be at least 8 characters"
                                    }
                                })}
                            />
                            {errors.new_password && (
                                <p className="text-red-500 text-sm mt-1">{errors.new_password.message}</p>
                            )}
                        </div>
                        
                        <div>
                            <label htmlFor="confirm_password" className="block text-sm font-medium text-gray-700 mb-1">
                                Confirm New Password
                            </label>
                            <input
                                type="password"
                                id="confirm_password"
                                className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-purple-500"
                                {...register("confirm_password", {
                                    required: "Please confirm your new password",
                                    validate: value => value === password.current || "The passwords do not match"
                                })}
                            />
                            {errors.confirm_password && (
                                <p className="text-red-500 text-sm mt-1">{errors.confirm_password.message}</p>
                            )}
                        </div>
                        
                        <button
                            type="submit"
                            disabled={isSubmitting}
                            className={`w-full py-2 px-4 border border-transparent rounded-md shadow-sm text-white bg-purple-600 hover:bg-purple-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-purple-500 ${
                                isSubmitting ? "opacity-50 cursor-not-allowed" : ""
                            }`}
                        >
                            {isSubmitting ? "Changing..." : "Change Password"}
                        </button>
                    </form>
                </div>
            </div>
    );
};

export default ChangePassword; 