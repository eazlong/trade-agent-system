import axios from "axios"
import API_URL from 'services'
import { getToken } from 'Utils/token';


const authLoginAPI = (params = {}) => {
    return {
        url: "jwt/create/",
        method: "post",
        //   headers: { accept: "*/*", Authorization: "Bearer " + getToken() },
        body: params,
    };
};

const callLoginAPI = (data) => {
    return axios.post(API_URL + 'jwt/create/', data)
        .then(async res => await res)
        .catch(async res => await res.response)
}

const fetchUserProfile = () => {
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

const callRegisterAPI = (data) => {
    return axios.post(API_URL + 'auth/users/', data)
        .then(async res => await res)
        .catch(async res => await res.response)
}

const callPasswordResetAPI = (data) => {
    return axios.post(API_URL + 'auth/password/reset/', data)
        .then(async res => await res)
        .catch(async res => await res.response)
}

const callPasswordResetConfirmAPI = (data) => {
    return axios.post(API_URL + 'auth/password/reset/confirm/', data)
        .then(async res => await res)
        .catch(async res => await res.response)
}

const callPasswordChangeAPI = (data) => {
    const token = getToken();
    return axios.post(
        API_URL + 'auth/password/change/', 
        data,
        {
            headers: {
                Authorization: `Bearer ${token}`
            }
        }
    )
    .then(async res => await res)
    .catch(async res => await res.response)
}

export {
    authLoginAPI,
    callLoginAPI,
    callRegisterAPI,
    callPasswordResetAPI,
    callPasswordResetConfirmAPI,
    callPasswordChangeAPI,
    fetchUserProfile
}