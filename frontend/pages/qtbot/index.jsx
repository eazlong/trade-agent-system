import React, { useState, useCallback, useEffect } from "react";
import useWebSocket, { ReadyState } from "react-use-websocket";
import Link from "next/link";
import {HOST} from "services/index"
import UserBotList from "Components/QtBot/UserBotList";
import { getUserBotConfigs } from "services/qtbot.service";
import { useToasts } from "react-toast-notifications";


const QtBots = () => {
  const { addToast } = useToasts();
  const [bots, setBots] = useState([]);
  const showToast = useCallback((message, appearance) => {
    if (addToast) {
      addToast(message, { appearance });
    }
  }, [addToast]);

  useEffect(()=>{
    const fetchData = async () => {
      try {
        const botConfigs = await getUserBotConfigs();
        setBots(botConfigs);
      } catch (error) {
        showToast(`Error fetching bot configs:${error}`, "error");
      }
    }

    fetchData();
  }, [showToast]);

  return <UserBotList bots={bots} />;
};

export default QtBots;
