from selenium.common.exceptions import TimeoutException, UnexpectedAlertPresentException
from sailer import Sailer
from selenium.common import exceptions
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import re
import boto3
import requests
import os
from PIL import Image
import json

DEFAULT_URL = "http://www.longtermcare.or.kr/"
START_URL = DEFAULT_URL + "npbs/r/a/201/selectLtcoSrch"
IMG_URL = DEFAULT_URL + "/npbs/e/d/101/selectPhotoStreamDocNo.web?atmtFileDocNo={img_no}"
S3_ENDPOINT = "https://s3.ap-northeast-2.amazonaws.com/carecell/{type}/{filename}"


class CarecellSailer(Sailer):
    def start(self):
        self.go(START_URL)

        # 서울(13) / 제주도(29)
        for city_num in range(20, 25):
            self.xpath(r'//*[@id="si_do_cd-button"]/span[1]').click()

            city_element = self.xpath(r'//*[@id="ui-id-{city_num}"]'.format(city_num=city_num))
            self.city = city_element.text
            city_element.click()

            self.parse_city()

    def parse_city(self):
        for i in range(1, 9):
            self.xpath(r'//*[@id="searchAdminKindCd-button"]/span[1]').click()

            service_html = self.xpath(r'//*[@id="searchAdminKindCd-menu"]').get_attribute('innerHTML')
            service_start_id = re.search(r'id="ui-id-(\d+).*전체', service_html).group(1)
            service_num = int(service_start_id) + i

            # if service_num > 57:
            #     service_num -= 55

            service_element = self.xpath(r'//*[@id="ui-id-{service_num}"]'.format(service_num=service_num))
            self.service = service_element.text

            if self.service == '노인요양시설':
                self.service += '(시설급여)'
            else:
                self.service += '(재가급여)'

            print(self.service)
            service_element.click()

            self.xpath(r'//*[@id="btn_search"]').click()

            self.parse_service()

    def parse_service(self):
        total = self.xpath(r'//*[@id="cont_wrap"]/div[3]/div[2]/div[8]/p/strong').text
        total_num = int(total.split('Total ')[1])

        next_button_num = (total_num - 1) // 100

        next_button = 0

        while next_button <= next_button_num:
            self.parse_10_pages()

            if next_button == next_button_num:
                break

            self.driver.find_element_by_class_name('page_next').click()
            next_button += 1

    def parse_10_pages(self):
        start_num = 1
        try:
            first_page = self.xpath(r'//*[@id="main_paging"]/a[1]').text

            if first_page == '처음':
                start_num += 2

        # 10페이지 이하일 때
        except exceptions.NoSuchElementException:
            print("There is no next button")

        # 첫 페이지(1, 11, 21...)
        self.parse_page()
        print('\n현재 page :', self.xpath(r'//*[@id="main_paging"]/em').text)

        for page in range(start_num, start_num + 9):
            try:
                next_page = self.xpath(r'//*[@id="main_paging"]/a[{page}]'.format(page=page))
                next_page.click()
                print('\n현재 page :', self.xpath(r'//*[@id="main_paging"]/em').text)

                # 첫 페이지 이후
                self.parse_page()

            except exceptions.NoSuchElementException:
                print('End of {city} {service}'.format(city=self.city, service=self.service))
                break

    def parse_page(self):
        service_type_elements = self.xpaths(r'//*[@id="ltco_info_list"]/tbody/tr[*]/td[2]')
        grade_elements = self.xpaths(r'//*[@id="ltco_info_list"]/tbody/tr[*]/td[3]')
        detail_url_elements = self.xpaths(r'//*[@id="ltco_info_list"]/tbody/tr[*]/td[4]/a')

        service_types = [service_type_element.text for service_type_element in service_type_elements]
        grades = [grade_element.text for grade_element in grade_elements]
        detail_urls = [detail_url_element.get_attribute('href') + "&showVlt=Y" for detail_url_element in
                       detail_url_elements]

        for service_type, self.grade, detail_url in zip(service_types, grades, detail_urls):
            print(detail_url)

            if '치매' in service_type:
                print('치매전담형\n')
                continue

            self.go(detail_url)

            try:
                self.parse_detail()

            except Exception as e:
                self.name = '(error)' + self.name
                self.data = {"url": detail_url, "Error message": str(e)}
                print('\n', self.data, '\n')

            # json 파일로 저장
            self.make_json()

            self.driver.back()

    def parse_detail(self):
        html = self.driver.page_source
        self.name = re.search(r'장기요양기관.*\s*<.*?>(.*)\(.*\)', html).group(1).strip()
        self.data = {
            "city": self.city,
            "service": self.service,
            "grade": self.grade,
            "name": self.name,
            "address": re.search(r'주소.*\s*<.*?>(.*)<\/td>', html).group(1).split('(')[0].strip(),
            "number": re.search(r'전화번호.*\s*<.*?>(.*)<\/td>', html).group(1).strip(),
            "founded_date": re.search(r'지정일자.*\s*<.*?>(.*)<\/td>', html).group(1).strip(),
            "max_people": re.search(r'정원\(A\).*\s*<.*?>(\s?)*(.*)(\s?)*<\/td>', html).group(2).strip(),
            "present_people": re.search(r'현원\(B\).*\s*<.*?>(\s?)*((.|\s)*?)<\/td>', html).group(2).strip(),
        }

        try:
            total_people_contained_doctor = self.xpath(r'//*[@id="equip1"]/div[1]/div[2]/table/tbody/tr/td[1]').text

            if not total_people_contained_doctor:
                self.data.update({
                    "social_worker": self.xpath(r'//*[@id="apprv1"]/div[1]/div[2]/table/tbody/tr/td[4]').text,
                    "nurse": self.xpath(r'//*[@id="apprv1"]/div[1]/div[2]/table/tbody/tr/td[5]').text,
                    "nurse_aide": self.xpath(r'//*[@id="apprv1"]/div[1]/div[2]/table/tbody/tr/td[6]').text,
                    "dental_hygienist": self.xpath(r'//*[@id="apprv1"]/div[1]/div[2]/table/tbody/tr/td[7]').text,
                    "1st_care_worker": self.xpath(r'//*[@id="apprv1"]/div[1]/div[2]/table/tbody/tr/td[10]').text,
                    "2nd_care_worker": self.xpath(r'//*[@id="apprv1"]/div[1]/div[2]/table/tbody/tr/td[11]').text,
                })

            else:
                self.data.update({
                    "social_worker": self.xpath(r'//*[@id="equip1"]/div[1]/div[2]/table/tbody/tr/td[4]').text,
                    "doctor": self.xpath(r'//*[@id="equip1"]/div[1]/div[2]/table/tbody/tr/td[5]').text,
                    "part_time_doctor": self.xpath(r'//*[@id="equip1"]/div[1]/div[2]/table/tbody/tr/td[6]').text,
                    "nurse": self.xpath(r'//*[@id="equip1"]/div[1]/div[2]/table/tbody/tr/td[7]').text,
                    "nurse_aide": self.xpath(r'//*[@id="equip1"]/div[1]/div[2]/table/tbody/tr/td[8]').text,
                    "dental_hygienist": self.xpath(r'//*[@id="equip1"]/div[1]/div[2]/table/tbody/tr/td[9]').text,
                    "1st_care_worker": self.xpath(r'//*[@id="equip1"]/div[1]/div[2]/table/tbody/tr/td[10]').text,
                    "2nd_care_worker": self.xpath(r'//*[@id="equip1"]/div[1]/div[2]/table/tbody/tr/td[11]').text,
                    "stay_care_worker": self.xpath(r'//*[@id="equip1"]/div[1]/div[2]/table/tbody/tr/td[12]').text,
                    "physical_therapist": self.xpath(r'//*[@id="equip1"]/div[1]/div[2]/table/tbody/tr/td[13]').text,
                    "occupational_therapist": self.xpath(r'//*[@id="equip1"]/div[1]/div[2]/table/tbody/tr/td[14]').text,
                })

        except exceptions.NoSuchElementException:
            print('There is no man power data')

        # services = re.search(r'제공서비스.*\s*<.*?>(.*)<\/td>', html).group(1).strip()
        # homepage_url = re.search(r'홈페이지주소체크(.*\s*)<a href="(.*?)"', html).group(2).strip()
        # traffic = re.search(r'교통편.*\s*<.*?>((.?\s?)*?)<\/td>', html).group(1).strip()

        photo_button = self.driver.find_element_by_class_name('btn_inner')
        photo_button.click()

        # 이미지 파싱 후 실내/외 분석
        self.driver.switch_to_window(self.driver.window_handles[1])

        try:
            image_data = self.parse_image()
            self.data.update(image_data)

            for key, value in self.data.items():
                print(key, ':', value)

        except Exception as e:
            print(str(e))

        self.close()
        self.driver.switch_to_window(self.driver.window_handles[0])

    def parse_image(self):
        image_data = {
            "outdoor_image_list": list(),
            "indoor_image_list": list()
        }

        while not self.alert_accept():
            self.alert_accept()

        view_button = self.xpath(r'//*[@id="C1"]/a')
        view_button.click()

        while not self.alert_accept():
            self.alert_accept()

        img_elements = self.xpaths(r'//*[@id="blbd_arti_vo"]/div[2]/ul/li[*]/div/a/img')
        img_name_elements = self.xpaths(r'//*[@id="blbd_arti_vo"]/div[2]/ul/li[*]/dl/dt/a')

        for img_element, img_name_element in zip(img_elements, img_name_elements):
            img_no = img_element.get_attribute('src').split('keyValue=')[1].strip()
            img_url = IMG_URL.format(img_no=img_no)

            filename = img_name_element.text.strip() + '.jpg'
            filename = '_'.join([self.city, self.service, filename])

            # s3 저장
            s3_url = self.download_to_s3(type='image', url=img_url, filename=filename)

            # 이미지 크기 0 or 다운로드 실패
            if s3_url is None:
                continue

            # 이미지 판단 api
            client = boto3.client('rekognition')

            response = client.detect_labels(Image={'S3Object': {'Bucket': 'carecell', 'Name': 'image/' + filename}},
                                            MinConfidence=55)

            image_type = 'outdoor'
            for label in response['Labels']:
                # print(label['Name'] + ' : ' + str(label['Confidence']))

                if label['Name'] in 'Indoors,Flooring,Floor':
                    image_type = 'indoor'

                elif label['Name'] in 'Human,Sign,Text,Poster,Logo':
                    image_type = label['Name']
                    break

            if image_type == 'outdoor':
                image_data['outdoor_image_list'].append(s3_url)
            elif image_type == 'indoor':
                image_data['indoor_image_list'].append(s3_url)

        return image_data

    def alert_accept(self):
        try:
            WebDriverWait(self.driver, 3).until(EC.alert_is_present(), 'time out')

            alert = self.driver.switch_to.alert
            alert.accept()
            return True

        except TimeoutException:
            print("no alert")
            return True

        except UnexpectedAlertPresentException:
            print("unexpected alert")
            return False

    def download_to_s3(self, **kwargs):
        filename = kwargs.get('filename', '')
        url = kwargs.get('url', '')
        try:
            if not filename:
                filename = url.split('/')[-1]

            filepath = r'./tmp/%s' % filename
            res = requests.get(url, stream=True)
            with open(filepath, "wb") as file:
                for chunk in res:
                    file.write(chunk)

            # 이미지 크기 0 일 때
            if os.path.getsize(filepath) == 0:
                os.remove(filepath)
                return

            image = Image.open(filepath)
            image.convert('RGB').save(filepath, "JPEG")

            s3 = boto3.resource('s3')

            s3.meta.client.upload_file(filepath, 'carecell', '{type}/{filename}'.format(**kwargs))
            s3_url = S3_ENDPOINT.format(**kwargs)

            os.remove(filepath)
            return s3_url

        except Exception as e:
            print(str(e))
            return

    def make_json(self):
        self.name = self.name.replace('/', '|')

        with open('./json2/{city}_{service}_{name}.json'.format(city=self.city, service=self.service, name=self.name),
                  'w', encoding='utf-8') as make_file:
            json.dump(self.data, make_file, ensure_ascii=False, indent='\t')


carecell_sailer = CarecellSailer()
carecell_sailer.start()
carecell_sailer.close()
