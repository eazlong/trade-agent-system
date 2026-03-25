import 'tailwindcss/tailwind.css'
import 'nprogress/nprogress.css'
import HeadScript from 'Components/Common/HeadScript'
import Layout from 'Layout'
import { store } from 'store'
import { Provider } from 'react-redux'
import { ToastProvider } from 'react-toast-notifications';
import Router, {useRouter} from 'next/router';
import Head from 'next/head';
import { useEffect } from 'react';
import { MantineProvider } from '@mantine/core';
import { ModalsProvider } from '@mantine/modals';
import '@mantine/core/styles.css';
import NProgress from 'nprogress';
import { getToken } from 'Utils/token';
import { saveToken, getUserProfile } from 'store/modules/auth';
import { useDispatch } from 'react-redux';

// Create a custom theme to match the original purple colors
const theme = {
  colors: {
    purple: [
      '#f3e8ff', // purple-100
      '#e9d5ff', // purple-200
      '#d8b4fe', // purple-300
      '#c084fc', // purple-400
      '#a855f7', // purple-500
      '#9333ea', // purple-600 - Header background
      '#7e22ce', // purple-700
      '#6b21a8', // purple-800 - Sidebar background
      '#581c87', // purple-900
      '#4c1d95', // purple-950
    ],
  },
  primaryColor: 'purple',
  primaryShade: 6,
};

NProgress.configure({ showSpinner: false });
Router.onRouteChangeStart = () => NProgress.start();
Router.onRouteChangeComplete = () => NProgress.done();
Router.onRouteChangeError = () => NProgress.done();

// 创建一个包装组件来处理用户数据的加载
function AppContent({ Component, pageProps }) {
  const router = useRouter();
  const dispatch = useDispatch();
  
  // 加载用户数据
  useEffect(() => {
    const token = getToken();
    if (token) {
      dispatch(saveToken(token));
      dispatch(getUserProfile());
    }
  }, [dispatch]);
  
  // Close mobile menu when route changes
  useEffect(() => {
    const handleRouteChange = () => {
      // Add class to body when mobile menu is closed
      document.body.classList.remove('overflow-hidden');
    };
    
    Router.events.on('routeChangeComplete', handleRouteChange);
    return () => {
      Router.events.off('routeChangeComplete', handleRouteChange);
    };
  }, []);
  
  return (
    <>
      <Head>
        <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no" />
      </Head>
      <HeadScript />
      {router.pathname.startsWith("/auth")?
      <Component {...pageProps} />:
      <Layout>
        <Component {...pageProps} />
      </Layout>
      }
    </>
  );
}

function MyApp({ Component, pageProps }) {
  return (
    <Provider store={store}>
      <MantineProvider theme={theme} defaultColorScheme="light">
        <ModalsProvider>
          <ToastProvider>
            <AppContent Component={Component} pageProps={pageProps} />
          </ToastProvider>
        </ModalsProvider>
      </MantineProvider>
    </Provider>
  );
}

export default MyApp
