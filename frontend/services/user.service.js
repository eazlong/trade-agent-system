import axios from "axios";
import API_URL from 'services';
import { getToken } from 'Utils/token';

// 获取用户个人资料
export const getUserProfile = () => {
    const token = getToken();
    return axios.get(
        API_URL + 'user/profile/',
        {
            headers: {
                Authorization: `Bearer ${token}`
            }
        }
    )
    .then(async res => await res)
    .catch(async res => await res.response);
};

// 更新用户个人资料
export const updateUserProfile = (data) => {
    const token = getToken();
    return axios.put(
        API_URL + 'user/profile/',
        data,
        {
            headers: {
                Authorization: `Bearer ${token}`
            }
        }
    )
    .then(async res => await res)
    .catch(async res => await res.response);
}; 