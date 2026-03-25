import API_URL, {apiClient} from 'services'

export const getTradeConifg = () => {
  return apiClient
    .get(API_URL + "trade/config/")
    .then(async (configs) => await configs.data);
};

export const createTradeConfig = (data) => {
  return apiClient
    .post(API_URL + `trade/config/`, data)
    .then(async (templates) => await templates.data);
};

export const updateTradeConifg = (id, data) => {
  return apiClient
    .put(API_URL + `trade/config/${id}/`, data)
    .then(async (templates) => await templates.data);
};

export const createOrder = (data) => {
  return apiClient
    .post(API_URL + `trade/order/`, data)
    .then(async (templates) => await templates.data);
};

export const getAllSymbols = () => {
  return apiClient
    .get(API_URL + `trade/symbols/`)
    .then(async (templates) => await templates.data);
};

export const getSymbol = (symbol) => {
  return apiClient
    .get(API_URL + `trade/symbols/${symbol}`)
    .then(async (templates) => await templates.data);
};

export const getKlines = (symbol, interval, start_time, end_time, config = {}) => {
  const url = API_URL + `trade/${symbol}/klines/${interval}/${start_time}/${end_time}`;
  const finalConfig = {
    // Treat 404/204 as a valid "no data" case to avoid noisy errors on initial load
    validateStatus: (status) => status === 200 || status === 404 || status === 204,
    ...config,
  };
  return apiClient
    .get(url, finalConfig)
    .then(async (response) => {
      if (response.status === 404 || response.status === 204) {
        return [];
      }
      return await response.data;
    });
}

export const getTradeRecords = (page, search, sort) => {
  return apiClient
    .get(API_URL + `trade/records/`, {
      params: {
        page: page,
        search: search,
        sort: sort,
      },
    })
    .then(async (templates) => await templates.data);
};