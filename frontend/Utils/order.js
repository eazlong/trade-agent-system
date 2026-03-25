export const side = (order) => {
    if (order.side.toLowerCase() == "long" && order.action.toLowerCase() == "buy") {
      return "开多";
    } else if (order.side.toLowerCase() == "long" && order.action.toLowerCase() == "sell") {
      return "平多";
    } else if (order.side.toLowerCase() == "short" && order.action.toLowerCase() == "sell") {
      return "开空";
    } else if (order.side.toLowerCase() == "short" && order.action.toLowerCase() == "buy") {
      return "平空";
    }
  };