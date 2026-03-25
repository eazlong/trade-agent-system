import React, { useState, useEffect } from "react";
import Header from "Components/Common/Header";
import Sidebar from "Components/Common/Siderbar";
import { useMediaQuery } from "@mantine/hooks";
import { toggleSidebar } from "store/modules/hideSidebar";
import { useDispatch, useSelector } from "react-redux";

const Layout = ({ children }) => {
  const [isMobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [isSidebarVisible, setSidebarVisible] = useState(true); // New state for sidebar visibility

  const isMobile = useMediaQuery('(max-width: 768px)');
  const dispatch = useDispatch();
  
  useEffect(() => {
    // Load sidebar visibility state from localStorage
    const savedVisibility = localStorage.getItem('sidebarVisible');
    if (savedVisibility !== null) {
      setSidebarVisible(savedVisibility === 'true');
    }
  }, []);

  const toggleMobileMenu = () => {
    setMobileMenuOpen(!isMobileMenuOpen);
  };

  const toggleSidebarVisibility = () => {
    const newVisibility = !isSidebarVisible;
    setSidebarVisible(newVisibility);
    localStorage.setItem('sidebarVisible', newVisibility.toString());
    dispatch(toggleSidebar());

  };

  return (
    <div className="flex flex-row h-screen bg-gray-100">
      <Header
        toggleMobileMenu={toggleMobileMenu}
        toggleSidebarVisibility={toggleSidebarVisibility} // Pass the new prop
        isSidebarVisible={isSidebarVisible} // Pass the new prop
      />

      {/* Sidebar */}
      {/* 使用 TailwindCSS 的 transition 和 opacity/translate-x 实现侧边栏的显示与隐藏动画 */}
      <div
        className={`
          fixed inset-y-0 top-16 left-0 z-30
          transition-all duration-500 ease-in-out
          ${isMobile
            ? isMobileMenuOpen
              ? "opacity-100 translate-x-0 pointer-events-auto"
              : "opacity-0 -translate-x-full pointer-events-none"
            : isSidebarVisible
              ? "opacity-100 translate-x-0 md:w-64 pointer-events-auto"
              : "opacity-0 -translate-x-20 md:w-64 pointer-events-none"
          }
        `}
        aria-label="侧边栏"
        tabIndex={isSidebarVisible || (isMobile && isMobileMenuOpen) ? 0 : -1}
      >
        <Sidebar
          toggleMobileMenu={toggleMobileMenu}
        />
      </div>

      {/* Main Content */}
      <main
        className={`flex-1 overflow-y-auto pt-16 transition-all duration-300 ease-in-out bg-purple-100
        ${
          isSidebarVisible
            ? "md:ml-64"
            : "md:ml-0"
        }`}
      >
        {children}
      </main>
    </div>
  );
};

export default Layout;