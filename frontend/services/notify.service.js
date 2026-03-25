import API_URL, {apiClient} from 'services'

export const getUserNotifyConfigs = () => {
  return apiClient
    .get(API_URL + "notify/configs/",)
    .then(async (configs) => await configs.data)
    //.catch(async res => await res.response);
};

export const getUserNotifyHistory = () => {
  return apiClient
    .get(API_URL + "notify/history/")
    .then(async (history) => await history.data)
    //.catch(async res => await res.response);
};

export const getUserNotifyTemplates = () => {
  return apiClient
    .get(API_URL + "strategy/templates/")
    .then(async (templates) => await templates.data)
    //.catch(async res => await res.response);
};

export const addUserNotifyConfig = (data) => {
  return apiClient
    .post(API_URL + "notify/configs/", data)
    .then(async (templates) => await templates.data)
    //.catch(async res => await res.response);
};

export const updateNotifyConfig = (id, data) => {
  return apiClient
    .put(API_URL + `notify/configs/${id}/`, data)
    .then(async (templates) => await templates.data)
    //.catch(async res => await res.response);
};


export const deleteNotifyConfig = (id) => {
  return apiClient
    .delete(API_URL + `notify/configs/${id}/`)
    .then(async (templates) => await templates.data)
    //.catch(async res => await res.response);
};