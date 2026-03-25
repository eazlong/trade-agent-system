// components/Sidebar.js
import React, { useState } from 'react';
import { NavLink, Collapse, Tooltip, Badge, ActionIcon } from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import {
  IconHome,
  IconBell,
  IconSettings,
  IconX,
  IconEdit,
  IconMail,
  IconArticle,
  IconChartBar,
  IconRobot,
  IconBook,
  IconUsers,
  IconShare,
} from '@tabler/icons-react';
import { useRouter } from 'next/router';

const Sidebar = ({ toggleMobileMenu }) => {
  const [openMenus, setOpenMenus] = useState({
    assistant: false,
    notify: false,
    quantitative: false,
    social: false,
    godbill: false,
  });
  const router = useRouter();
  const isMobile = useMediaQuery('(max-width: 768px)');

  const handleSubMenuClick = (menuKey) => {
    setOpenMenus((prev) => ({
      ...prev,
      [menuKey]: !prev[menuKey],
    }));
  };

  const handleNavigation = (path) => {
    router.push(path);
    if (isMobile && toggleMobileMenu) {
      toggleMobileMenu();
    }
  };

  const isActivePath = (path) => router.pathname === path;

  const renderMenuItem = (icon, text, path, badgeCount = null) => {
    const isActive = isActivePath(path);
    const item = (
      <NavLink
        key={path}
        label={text}
        leftSection={icon}
        onClick={() => handleNavigation(path)}
        active={isActive}
        autoContrast
        className={`rounded-lg transition-all duration-200`}
        rightSection={
          badgeCount !== null ? <Badge color="white" circle>{badgeCount}</Badge> : null
        }
       
      />
    );

    return isMobile ? item : <Tooltip key={path} label={text} position="right" withArrow>{item}</Tooltip>;
  };

  const renderSubMenu = (icon, text, menuKey, items, badgeCount = null) => {
    const isOpen = openMenus[menuKey];
    
    return (
      <NavLink
        key={menuKey}
        label={text}
        leftSection={badgeCount !== null ? <Badge color="red" circle>{badgeCount}</Badge> : icon}
        onClick={() => handleSubMenuClick(menuKey)}
        className="rounded-lg transition-all duration-200 mt-2 "
        childrenOffset={28}
        opened={isOpen}
      >
        {items.map((item) => (
          <NavLink
            key={item.path}
            label={item.text}
            onClick={(e) => {
              e.stopPropagation();
              handleNavigation(item.path);
            }}
            active={isActivePath(item.path)}
           
            className={`rounded-lg transition-all duration-200`}
          />
        ))}
      </NavLink>
    );
  };

  const menuItems = [
    {
      type: 'submenu',
      icon: <IconRobot size="1.2rem" />,
      text: '辅助工具',
      menuKey: 'assistant',
      items: [
        { text: '交易系统', path: '/assistant/system' },
        { text: '交易计划', path: '/assistant/plan' },
        { text: '交割单复盘', path: '/assistant/summary' },
        { text: '编辑器模板', path: '/assistant/templates' },
      ],
    },
    {
      type: 'menuitem',
      icon: <IconUsers size="1.2rem" />,
      text: '社交中心',
      path: '/social',
    },
    {
      type: 'submenu',
      icon: <IconBook size="1.2rem" />,
      text: '大牛交割单',
      menuKey: 'godbill',
      items: [{ text: 'bit浪交割单', path: '/godbill/bitlang' }],
    },
    {
      type: 'submenu',
      icon: <IconBell size="1.2rem" />,
      text: '通知',
      menuKey: 'notify',
      items: [
        { text: '实时通知', path: '/notify' },
        { text: '通知设置', path: '/notify/config' },
      ],
    },
    {
      type: 'submenu',
      icon: <IconChartBar size="1.2rem" />,
      text: '量化',
      menuKey: 'quantitative',
      items: [
        { text: '量化机器人', path: '/qtbot' },
        { text: '量化设置', path: '/qtbot/config' },
      ],
    },
  ];

  return (
    <div
      className={` h-full flex-shrink-0 flex-grow-0 max-h-screen md:max-h-none overflow-auto bg-gray-50 ${
        !isMobile ? 'md:px-2' : 'p-2'
      }`}
    >
      <div className="flex justify-between items-center mb-4 md:hidden">
        <span className="font-bold text-lg">Menu</span>
        <ActionIcon onClick={toggleMobileMenu} variant="transparent">
          <IconX size="1.2rem" color="white" />
        </ActionIcon>
      </div>

      <div className="flex flex-col gap-y-1">
        {menuItems.map((item, index) => {
          if (item.type === 'submenu') {
            return renderSubMenu(item.icon, item.text, item.menuKey, item.items);
          }
          return renderMenuItem(item.icon, item.text, item.path);
        })}
      </div>
    </div>
  );
};

export default Sidebar;
