import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/router";
import { useDispatch } from "react-redux";
import { removeToken } from "Utils/token";
import { clearToken } from "store/modules/auth";

function Index() {
    const [show, setShow] = useState(false);
    const router = useRouter();
    const dispatch = useDispatch();
    
    const handleLogout = () => {
        removeToken();
        dispatch(clearToken());
        router.push("/auth/login");
        setShow(false);
    };
    
    return (
        <>
            {/* Code block starts */}
            {show && (
                <div id="popover" className="transition duration-150 ease-in-out -mt-16 absolute top-0 left-0 ml-20 w-full sm:w-1/2 z-50">
                    <div className="w-full bg-purple-100 rounded shadow-2xl">

                        <div className="w-full h-full px-4 xl:px-8 py-5">

                            <hr className="my-5 border border-gray-200" />
                            <div className="w-full h-full pb-5 lg:pb-10">
                                <Link href="/user/profile" onClick={() => setShow(false)}>
                                    <div className="flex justify-between items-center cursor-pointer hover:bg-gray-100 p-2 rounded">
                                        <div className="flex items-center">
                                            <h3 className="mb-2 sm:mb-1 text-gray-800 text-base font-normal leading-4">
                                                My Account
                                            </h3>
                                        </div>
                                    </div>
                                </Link>
                                <hr className="my-5 border border-gray-200" />
                                <Link href="/user/change-password" onClick={() => setShow(false)}>
                                    <div className="flex justify-between items-center cursor-pointer hover:bg-gray-100 p-2 rounded">
                                        <div className="flex items-center">
                                            <h3 className="mb-2 sm:mb-1 text-gray-800 text-base font-normal leading-4">
                                                Change Password
                                            </h3>
                                        </div>
                                    </div>
                                </Link>
                                <hr className="my-5 border border-gray-200" />
                                <Link href="/user/orders" onClick={() => setShow(false)}>
                                    <div className="flex justify-between items-center cursor-pointer hover:bg-gray-100 p-2 rounded">
                                        <div className="flex items-center">
                                            <h3 className="mb-2 sm:mb-1 text-gray-800 text-base font-normal leading-4">
                                                My Orders
                                            </h3>
                                        </div>
                                    </div>
                                </Link>
                                <hr className="my-5 border border-gray-200" />
                                <Link href="/user/payments" onClick={() => setShow(false)}>
                                    <div className="flex justify-between items-center cursor-pointer hover:bg-gray-100 p-2 rounded">
                                        <div className="flex items-center">
                                            <h3 className="mb-2 sm:mb-1 text-gray-800 text-base font-normal leading-4">
                                                My Payment History
                                            </h3>
                                        </div>
                                    </div>
                                </Link>
                                <hr className="my-5 border border-gray-200" />
                                <Link href="/notify/config" onClick={() => setShow(false)}>
                                    <div className="flex justify-between items-center cursor-pointer hover:bg-gray-100 p-2 rounded">
                                        <div className="flex items-center">
                                            <h3 className="mb-2 sm:mb-1 text-gray-800 text-base font-normal leading-4">
                                                Notifications
                                            </h3>
                                        </div>
                                    </div>
                                </Link>
                                <hr className="my-5 border border-gray-200" />
                                <div 
                                    className="flex justify-between items-center cursor-pointer hover:bg-gray-100 p-2 rounded"
                                    onClick={handleLogout}
                                >
                                    <div className="flex items-center">
                                        <h3 className="mb-2 sm:mb-1 text-gray-800 text-base font-normal leading-4">
                                            Logout
                                        </h3>
                                    </div>
                                </div>
                                <hr className="my-5 border border-gray-200" />
                            </div>
                        </div>
                    </div>
                </div>
            )}
            <button 
                onClick={() => setShow(!show)}
                className="focus:outline-none"
            >
                <svg xmlns="http://www.w3.org/2000/svg" className="h-6 w-6 text-gray-600 hover:text-gray-800" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
                </svg>
            </button>
            {/* Code block ends */}
        </>
    );
}
export default Index;
