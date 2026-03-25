import React, { useState, useEffect } from "react";
import NavTitle from "../UI/NavTitle";
import HeaderRight from "./HeaderRight";
import { useRouter } from "next/router";
import { IconMenu2, IconEye, IconEyeOff } from '@tabler/icons-react';
import { ActionIcon, Tooltip } from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';

const Header = ({ toggleMobileMenu, toggleSidebarVisibility, isSidebarVisible }) => {
  const isMobile = useMediaQuery('(max-width: 768px)');

  return (
    <header className="fixed top-0 left-0 w-full bg-purple-600 text-white p-1 flex justify-between items-center shadow-md z-40">
      <nav className="px-4 sm:px-6 w-full mx-auto flex justify-between items-center h-14">
        <div className="flex items-center space-x-4">
          {isMobile ? (
            <ActionIcon
              variant="transparent"
              color="white"
              size="lg"
              onClick={toggleMobileMenu}
              className="mr-2"
            >
              <IconMenu2 size={24} />
            </ActionIcon>
          ) : (
            <Tooltip label={isSidebarVisible ? "隐藏侧边栏" : "显示侧边栏"}>
              <ActionIcon
                variant="transparent"
                color="white"
                size="lg"
                onClick={toggleSidebarVisibility}
                className="mr-2"
              >
                {isSidebarVisible ? <IconEyeOff size={24} /> : <IconEye size={24} />}
              </ActionIcon>
            </Tooltip>
          )}
          <NavTitle />
        </div>
        <HeaderRight />
      </nav>
    </header>
  );
};

export default Header;