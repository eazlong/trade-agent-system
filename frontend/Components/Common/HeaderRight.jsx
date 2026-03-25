import { useEffect, useState } from 'react';
import Link from 'next/link'
import { useDispatch, useSelector } from 'react-redux';
import { getToken, removeToken } from 'Utils/token';
import { useRouter } from 'next/router';
import { saveToken } from 'store/modules/auth';
import MyAccountPopover from "Components/UI/MyAccountPopover";
import { IconUser, IconSettings } from '@tabler/icons-react';
import { Menu, Badge, ActionIcon, Tooltip } from '@mantine/core';

const HeaderRight = () => {
    const router = useRouter()
    const { accessToken } = useSelector((state) => state.auth)
    const dispatch = useDispatch()
    const [anchorEl, setAnchorEl] = useState(null);
    const open = Boolean(anchorEl);
    
    const handleClick = (event) => {
      setAnchorEl(event.currentTarget);
    };
    
    const handleClose = () => {
      setAnchorEl(null);
    };

    useEffect(() => {
        const token = getToken()
        if (token) dispatch(saveToken(token))
    }, [accessToken])

    const logoutProcess = () => {
        removeToken()
        router.push('/auth/login')
        dispatch(saveToken(null))
        handleClose()
    }
    
    return (
      <div className="flex items-center space-x-2 md:space-x-4">
        {accessToken ? (
          <>
            {/* Settings icon */}
            <Tooltip label="设置交易所API Key">
              <ActionIcon
                variant="transparent"
                color="white"
                size="lg"
                onClick={() => {
                  router.push("/user/tradeconfig");
                }}
                className="hover:bg-purple-800"
              >
                <IconSettings size={20} />
              </ActionIcon>
            </Tooltip>

            {/* User account */}
            <div className="flex items-center">
              <Menu shadow="md" width={200} position="bottom-end">
                <Menu.Target>
                  <button
                    className="flex items-center space-x-1 text-white hover:bg-purple-800 rounded-lg px-2 py-1"
                  >
                    <Badge size="sm" variant="filled" count={0}>
                      <IconUser size={20} />
                    </Badge>
                    <span className="hidden md:inline-block text-sm">
                      {localStorage.getItem("username")}
                    </span>
                  </button>
                </Menu.Target>

                <Menu.Dropdown>
                  <Menu.Item
                    onClick={() => {
                      router.push("/user/profile");
                    }}
                  >
                    修改用户信息
                  </Menu.Item>
                  <Menu.Item
                    onClick={() => {
                      router.push("/user/change-password");
                    }}
                  >
                    修改密码
                  </Menu.Item>
                  <Menu.Item onClick={logoutProcess} color="red">
                    退出
                  </Menu.Item>
                </Menu.Dropdown>
              </Menu>
            </div>
          </>
        ) : (
          <>
            <Link
              href="/auth/login"
              className="text-white hover:text-gray-200 text-sm md:text-base"
            >
              Sign In
            </Link>
            <Link href={"/auth/register"}>
              <button className="bg-purple-100 text-purple-900 hover:bg-gray-100 py-1 px-3 md:px-4 rounded-md text-sm md:text-base font-medium">
                Sign Up
              </button>
            </Link>
          </>
        )}
      </div>
    );
}

export default HeaderRight;