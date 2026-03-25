import React, { useEffect, createRef, useState } from "react";


const TradingView = ({ market, code, screenshot }) => {
  const [widget, setWidget] = useState(null);
  const tvRef = createRef();
  const tvUrl = "https://s3.tradingview.com/tv.js";
  const handleInit = () => {
    const TV = window.TradingView;
    const w = new TV.widget({
      autosize: true,
      symbol: `${market}:${code}`,
      interval: "15",
      timezone: "Asia/Shanghai",
      theme: "dark",
      style: "1",
      locale: "zh_CN",
      toolbar_bg: "#f1f3f6",
      enable_publishing: false,
      allow_symbol_change: false,
      // studies: ["RSI@tv-basicstudies"],
      // save_image: false,
      container_id: `${market}:${code}`,
      hide_side_toolbar: false,
      disabled_features: ["use_localstorage_for_settings"],
    });
    setWidget(w);
  };

  useEffect(() => {
    if (!market || !code) return;
    const script = document.createElement("script");
    script.src = tvUrl;
    script.async = true;
    script.onload = handleInit;
    tvRef.current.appendChild(script);
  }, [market, code]);

  useEffect(() => {
    if (!screenshot) return;
    handleScreenshot();
  }, [screenshot]);

  const handleScreenshot = async () => {
    if (!widget) return;
    console.log(widget);
    const screenshotCanvas = await widget.save_image;
    console.log(screenshotCanvas);
    // const linkElement = document.createElement("a");
    // linkElement.download = "screenshot";
    // linkElement.href = screenshotCanvas.toDataURL(); // Alternatively, use `toBlob` which is a better API
    // linkElement.dataset.downloadurl = [
    //   "image/png",
    //   linkElement.download,
    //   linkElement.href,
    // ].join(":");
    // document.body.appendChild(linkElement);
    // linkElement.click();
    // document.body.removeChild(linkElement);
  };

  return (
    <div
      className="tradingview-widget-container"
      ref={tvRef}
      style={{ height: "100%" }}
    >
      <div id={`${market}:${code}`} style={{ height: "100%" }}></div>
    </div>
  );
};
export default TradingView;
