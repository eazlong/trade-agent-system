// TradeConfigForm.js
import React, { useState, useEffect } from "react";
import {
  getTradeConifg,
  updateTradeConifg,
  createTradeConfig,
} from "services/trade.service";
import { TextInput, Button, Box, Checkbox, Select } from "@mantine/core";

import { useToasts } from 'react-toast-notifications'

const TradeConfigForm = ({ userId }) => {
  const {addToast} = useToasts()
  const [tradeConfig, setTradeConfig] = useState({
    id: 0,
    exchange_type: "binance",
    key: "",
    secret: "",
    password: "",
    is_sandbox: false,
  });


  const [existingConfig, setExistingConfig] = useState(null);

  useEffect(() => {
    const fetchData = async () => {
      try {
        const tradeConfig = await getTradeConifg();
        if (tradeConfig != undefined) {
          setTradeConfig(tradeConfig);
          setExistingConfig(true)
        }
      } catch (error) {
        addToast(`Failed to fetch trade configuration: ${error}`, { appearance: 'error', autoDismiss: true });
      }
    };

    fetchData();
  }, []);

  const handleChange = (e) => {
    const { name, value } = e.target;
    console.log(name, value);
    setTradeConfig({
      ...tradeConfig,
      [name]: value,
    });
  };


  const handleSubmit = async (e) => {
    e.preventDefault();
    
    try {
        if (existingConfig) {
            await updateTradeConifg(tradeConfig.id, tradeConfig);
        } else {
            const response = await createTradeConfig(tradeConfig);
            setExistingConfig(response.data);
        }
        addToast('Trade configuration saved successfully!', { appearance: 'success', autoDismiss: true });
    } catch (error) {
        addToast(`Failed to save trade configuration: ${error}`, { appearance: 'error', autoDismiss: true });
    }
  };

  return (
    <Box
      component="form"
      onSubmit={handleSubmit}
      className="p-4 bg-purple-100 shadow-md rounded"
    >
      <div className="mb-4">
        <Select
          label="交易所类型"
          placeholder="请选择交易所"
          name="exchange_type"
          value={tradeConfig.exchange_type}
          onChange={(value) => {
            setTradeConfig({
              ...tradeConfig,
              exchange_type: value,
            });
          }}
          data={[
            { value: "binance", label: "币安" },
            { value: "okx", label: "欧易" }
          ]}
          required
        />
      </div>
      <div className="mb-4">
        <TextInput
          label="API Key"
          name="key"
          value={tradeConfig.key}
          onChange={handleChange}
          required
        />
      </div>
      <div className="mb-4">
        <TextInput
          label="API Secret"
          name="secret"
          value={tradeConfig.secret}
          onChange={handleChange}
          type="password"
          required
        />
      </div>
      {tradeConfig.exchange_type == "okx" && (
        <div className="mb-4">
        <TextInput
          label="API Password"
          name="password"
          value={tradeConfig.password}
          onChange={handleChange}
          type="password"
          required
        />
      </div>
      )}
      <div className="mb-4">
        <Checkbox
          label="Testnet"
          checked={tradeConfig.is_sandbox}
          onChange={(event) => {
            setTradeConfig({
              ...tradeConfig,
              is_sandbox: event.currentTarget.checked,
            });
          }}
        />
      </div>
      <Button
        type="submit"
        variant="filled"
        color="blue"
        fullWidth
      >
        Save
      </Button>
    </Box>
  );
};

export default TradeConfigForm;