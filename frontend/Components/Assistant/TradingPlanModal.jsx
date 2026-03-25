// Import necessary components and libraries
import React, { useState, createRef, useEffect } from "react";
import { Modal, Button, Box, ActionIcon, Title, Select } from "@mantine/core";
import { IconX } from '@tabler/icons-react';
import Editor from "./Editor"; 
import ConditionCheck from "./ConditionCheck";
import  Link  from "next/link";
import TradingView from "../TradingView/TradingView";
import { getPlan, uploadScreenshot } from "../../services/assistant.service";

const TradingPlanModal = ({ symbol, open, onClose, userPlanContent, userCheckCondition, date=null }) => {

  const [selectedDate, setSelectedDate] = useState(date ? date : (new Date().toISOString().split('T')[0]));
  const [userContent, setUserPlanContent] = useState(userPlanContent);
  const [userCondition, setUserCondition] = useState(userCheckCondition);

  useEffect(() => {
    setSelectedDate(date ? date : (new Date().toISOString().split('T')[0]));
  }, [date]);

  useEffect(() => {
    setUserPlanContent(userPlanContent);
  }, [userPlanContent]);

  useEffect(() => {
    setUserCondition(userCheckCondition);
  }, [userCheckCondition]);

  const handleClose = () => {
    if (onClose) {
      onClose();
    }
  };
  
  const onDateChange = async (value) => {
    if (value == selectedDate) {
      return
    }
    
    setSelectedDate(value);
    const response = await getPlan(symbol, value);
    if (response.status === 200) {
      if (response.data.content) {
        setUserPlanContent(response.data.content);
      } 
      if (response.data.condition) {
        setUserCondition(response.data.condition);
      }
    }
  };
  
  // Generate date options for last 30 days
  const dateOptions = Array.from({ length: 30 }, (_, i) => {
    const date = new Date();
    date.setDate(date.getDate() - i);
    const formattedDate = `${String(
      date.getFullYear()
    )}-${String(date.getMonth() + 1).padStart(
      2,
      "0"
    )}-${String(date.getDate()).padStart(2, "0")}`;
    return { value: formattedDate, label: formattedDate };
  });

  return (
    <Modal 
      opened={open} 
      onClose={onClose}
      size="90%"
      title="交易计划"
      padding="lg"
    >
      <div className="flex flex-col h-full" style={{ height: '600px' }}>
        <div className="m-1 flex flex-row items-center justify-between mb-4">
          <div className="flex flex-row items-center">
            <span className="mr-2 font-medium">选择日期:</span>
            <div className="ml-2" style={{ minWidth: 120 }}>
              <Select
                value={selectedDate}
                onChange={onDateChange}
                data={dateOptions}
                size="sm"
              />
            </div>
          </div>
          {symbol !== "ALL" && (
            <Link
              href={`/tradingview/${symbol}.P`}
              target="_blank"
              className="flex-initial w-14 text-purple-600 hover:text-purple-800"
            >
              k线图
            </Link>
          )}
        </div>
        <div className="flex-1 overflow-auto" style={{ maxHeight: '540px' }}>
          {/* <ConditionCheck conditions={userCondition} /> */}
          <Editor contentData={userContent} />
        </div>
      </div>
    </Modal>
  );
};

export default TradingPlanModal;