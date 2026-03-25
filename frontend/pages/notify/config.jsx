import MyNotifyConfigsTable from "Components/Notify/UserNotifyConfigs";
// import { useEffect } from "react";
// import { useDispatch } from "react-redux";
// import {
//   fetchNotifyConfigs,
//   fetchNotifyConfigTemplates,
// } from "store/modules/notify";

const UserNotifyConfigList = () => {
    // const dispatch = useDispatch();
    // useEffect(() => {
    //   /** 1. Fetching Initial Categories and Products */
    //   // dispatch(fetchCategory())
    //   // dispatch(fetchProducts())
    //   // dispatch(fetchNotifyConfigs());      
    //   // dispatch(fetchNotifyConfigTemplates());
    // }, []);

  return <MyNotifyConfigsTable />;
};

export default UserNotifyConfigList;
