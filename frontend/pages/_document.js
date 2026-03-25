import Document, { Html, Head, Main, NextScript } from 'next/document'
import { ColorSchemeScript, mantineHtmlProps } from '@mantine/core';
class MyDocument extends Document {
  render() {
    return (
      <Html lang="en" {...mantineHtmlProps}>
        <Head>
          <ColorSchemeScript defaultColorScheme="auto" />
          <meta charSet="utf-8" />
          <meta name="theme-color" content="#000000" />
          <meta name="description" content="Trading Helper System" />
          <meta name="apple-mobile-web-app-capable" content="yes" />
          <meta name="apple-mobile-web-app-status-bar-style" content="black" />
          <meta name="format-detection" content="telephone=no" />
          <link rel="preconnect" href="https://fonts.googleapis.com" />
          <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
          <link
            href="https://fonts.googleapis.com/css2?family=Rubik:ital,wght@0,400;0,500;0,600;0,700;1,300&display=swap"
            rel="stylesheet"
          />
        </Head>
        <body className="antialiased">
          <Main />
          <NextScript />
        </body>
      </Html>
    )
  }
}

export default MyDocument